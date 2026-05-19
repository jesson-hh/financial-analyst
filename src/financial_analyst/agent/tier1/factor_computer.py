from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
import pandas as pd
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.factors.core import compute_factors
from financial_analyst.factors.whale import compute_whale_signals
from financial_analyst.factors.sentiment import score_board, compute_vol_regime
from financial_analyst.data.loader_factory import get_default_loader


class FactorOutput(BaseModel):
    code: str
    asof_date: str
    factor_scores: Dict[str, float]
    whale_signals: Dict[str, Any]
    board_score: Dict[str, Any]
    vol_regime: Dict[str, Any]
    zoo_signals: Dict[str, Any] = {}  # v1.3.4: latest snapshot lookup, optional


class FactorComputer(SubAgent[FactorOutput]):
    NAME = "factor-computer"
    OUTPUT_SCHEMA = FactorOutput

    def __init__(self, memory_root, loader=None):
        super().__init__(memory_root=memory_root)
        self._loader = loader

    def _get_loader(self):
        return self._loader or get_default_loader()

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

        # ------------------------------------------------------------------
        # Auto-fetch 5min bars where the loader supports them.
        # Failures are silently swallowed — v5/R11 enrichment is best-effort.
        # ------------------------------------------------------------------
        bars_5m_last_day: Optional[pd.DataFrame] = None
        bars_5m_on_board_day: Optional[pd.DataFrame] = None

        if quote is not None and not quote.empty:
            last_day = pd.Timestamp(quote["trade_date"].iloc[-1]).strftime("%Y-%m-%d")

            # 1. Last-day 5min bars — feed into compute_vol_regime (R11 tail_surge)
            try:
                bars = loader.fetch_quote(code, last_day, last_day, freq="5min")
                if bars is not None and not bars.empty:
                    bars_5m_last_day = bars
            except Exception:
                pass  # loader doesn't support 5min or data absent — that's fine

            # 2. Most recent limit-up day in the lookback window — feed into
            #    score_board v5 (seal_micro dimension).
            if "close" in quote.columns and len(quote) >= 2:
                pct_chg = quote["close"].pct_change()
                limit_up_mask = pct_chg >= 0.095
                limit_up_days = quote.loc[limit_up_mask]
                if not limit_up_days.empty:
                    board_day = pd.Timestamp(
                        limit_up_days["trade_date"].iloc[-1]
                    ).strftime("%Y-%m-%d")
                    try:
                        bars = loader.fetch_quote(code, board_day, board_day, freq="5min")
                        if bars is not None and not bars.empty:
                            bars_5m_on_board_day = bars
                    except Exception:
                        pass

        board = score_board(
            quote,
            turnover_rate=tr_latest,
            market_cap_yi=mv_yi,
            bars_5m_on_board_day=bars_5m_on_board_day,
        )
        regime = compute_vol_regime(
            close_day=quote["close"],
            turnover_rate_day=(
                db["turnover_rate"]
                if db is not None and not db.empty
                else quote["vol"] / quote["vol"].mean() * 5
            ),
            bars_5m_last_day=bars_5m_last_day,
        )

        # v1.3.4: look up most-recent zoo snapshot (best-effort, silent on miss)
        zoo_signals: Dict[str, Any] = {}
        try:
            from financial_analyst.factors.zoo.snapshot import load_snapshot_for_code
            # Try the canonical universe first, fall back to others if absent
            for universe_name in ("csi300_active", "csi300_2024h2", "sample30"):
                sub = load_snapshot_for_code(universe_name, code, asof_or_earlier=asof)
                if sub is not None:
                    zoo_signals = {
                        "snapshot_universe": universe_name,
                        "snapshot_asof": str(sub["snapshot_asof"].iloc[0]),
                        "alphas": {
                            row["alpha"]: {
                                "value": float(row["value"]),
                                "rank_pct": float(row["rank_pct"]),
                                "universe_n": int(row["n_obs"]),
                            }
                            for _, row in sub.iterrows()
                        },
                    }
                    break
        except Exception:
            pass  # snapshot module missing or read error — silent skip

        return {
            "code": code, "asof_date": asof,
            "factor_scores": {k: (float(v) if v is not None and v == v else 0.0) for k, v in factors.items()},
            "whale_signals": whale,
            "board_score": board,
            "vol_regime": regime,
            "zoo_signals": zoo_signals,
        }
