"""TradeLog — fill stream + Outcome records for the P1 backtest engine.

Holds the raw ``Fill`` stream plus a list of ``Outcome`` (decision → measured
result, aligned with ``dream.outcome_tracker.Outcome``). ``trade_stats``
computes per-trade win rate / profit factor from realized PnL (paired
buy→sell round trips), distinct from the up-day ratio that ``portfolio_stats``
reports.

``_action_verdict`` is copied inline (synced from
``dream.outcome_tracker._action_verdict``) ON PURPOSE: importing
``financial_analyst.dream`` pulls in litellm via ``dream/__init__.py``, and this
backtest subpackage must stay zero-LLM / zero-network. The function is 26 lines
of pure if/else — keep it in sync manually if the dream version changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from financial_analyst.backtest.portfolio import Fill


def _action_verdict(action: str, return_5d: float, hit_target: bool, hit_stop: bool) -> str:
    """Map predicted action × actual outcome → verdict.

    Synced from ``financial_analyst.dream.outcome_tracker._action_verdict``
    (do NOT import dream here — it pulls litellm).

    Rules:
      buy:    return_5d > 2% OR hit_target → correct, hit_stop OR < -2% → wrong, else partial
      hold:   -2% ≤ return_5d ≤ 2% → correct, else partial
      sell:   return_5d < 0 → correct, else wrong
      avoid:  return_5d ≤ 0 → correct, else partial
    """
    if hit_stop:
        return "wrong"
    if action == "buy":
        if hit_target or return_5d > 0.02:
            return "correct"
        if return_5d < -0.02:
            return "wrong"
        return "partial"
    if action == "hold":
        if -0.02 <= return_5d <= 0.02:
            return "correct"
        return "partial"
    if action == "sell":
        return "correct" if return_5d < 0 else "wrong"
    if action == "avoid":
        return "correct" if return_5d <= 0 else "partial"
    return "partial"


@dataclass
class Outcome:
    """One historical prediction + its measured outcome.

    Field-for-field aligned with ``dream.outcome_tracker.Outcome`` (so a
    backtest Outcome can flow into the same downstream consumers). ``rating_overall``
    has no default in the source dataclass — for rule-signal backtests it has no
    rating semantics, so pass ``rating_overall=0`` as a placeholder.
    """

    code: str
    asof_date: str
    rating_overall: int
    action: str
    target_price: float
    stop_loss: float
    position_pct: float
    actual_close_t5d: Optional[float] = None
    actual_close_t20d: Optional[float] = None
    high_t1_t5d: Optional[float] = None
    low_t1_t5d: Optional[float] = None
    return_t5d: Optional[float] = None
    return_t20d: Optional[float] = None
    hit_target_within_5d: Optional[bool] = None
    hit_stop_within_5d: Optional[bool] = None
    verdict: str = "pending"
    summary_json: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        from dataclasses import asdict

        return asdict(self)


@dataclass
class TradeLog:
    fills: List[Fill] = field(default_factory=list)
    outcomes: List[Outcome] = field(default_factory=list)

    def add_fill(self, fill: Optional[Fill]) -> None:
        if fill is not None:
            self.fills.append(fill)

    def add_outcome(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    def trade_stats(self) -> Dict[str, float]:
        """Per-trade stats from realized PnL on sell fills (closed round trips).

        Returns ``{trade_win_rate, profit_factor, n_trades, avg_win, avg_loss}``.
        A "trade" here is a realizing sell fill. Distinct from the up-day ratio
        reported by ``portfolio_stats``.
        """
        nan = float("nan")
        sells = [f for f in self.fills if f.side == "sell"]
        n = len(sells)
        if n == 0:
            return {"trade_win_rate": nan, "profit_factor": nan, "n_trades": 0,
                    "avg_win": nan, "avg_loss": nan}
        wins = [f.realized_pnl for f in sells if f.realized_pnl > 0]
        losses = [f.realized_pnl for f in sells if f.realized_pnl < 0]
        gross_win = sum(wins)
        gross_loss = -sum(losses)
        trade_win_rate = len(wins) / n
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (
            float("inf") if gross_win > 0 else nan)
        avg_win = (gross_win / len(wins)) if wins else nan
        avg_loss = (-gross_loss / len(losses)) if losses else nan
        return {
            "trade_win_rate": trade_win_rate,
            "profit_factor": profit_factor,
            "n_trades": n,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
        }
