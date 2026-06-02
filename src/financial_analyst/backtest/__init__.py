"""P1 backtest engine — pure matching/ledger, no LLM, no network, no stocks import.

Inputs: raw 5min/day bars + a stream of orders/rule signals.
Outputs: fills + positions + a NAV series + return metrics.

Reuses ``financial_analyst.factors.eval.portfolio.portfolio_stats`` /
``PortfolioResult`` for metrics (does not modify them). This subpackage is a
leaf — ``factors.eval`` does not import it, so there is no cycle.
"""
from __future__ import annotations

from financial_analyst.backtest.costs import CostModel
from financial_analyst.backtest.limits import (
    compute_ref_prev_close,
    is_one_word,
    limit_pct_for,
)
from financial_analyst.backtest.broker import Broker, Order
from financial_analyst.backtest.portfolio import Fill, Position, VirtualPortfolio
from financial_analyst.backtest.metrics import compute_metrics
from financial_analyst.backtest.records import Outcome, TradeLog, _action_verdict

# --- P2: pre-open decision agent + candidate pool + end-to-end runner --------
from financial_analyst.backtest.pit_reader import (
    EventItem,
    NewsItem,
    PitReader,
    VisibleInfo,
)
from financial_analyst.backtest.candidate import (
    CandidateConfig,
    CandidateResult,
    select_candidates,
)
from financial_analyst.backtest.decision import (
    Decision,
    DecisionAgent,
    DecisionCache,
    DecisionInput,
    DecisionLeg,
)
from financial_analyst.backtest.engine import (
    BacktestResult,
    BacktestRunner,
    RunConfig,
)

__all__ = [
    "VirtualPortfolio",
    "Position",
    "Broker",
    "Order",
    "Fill",
    "CostModel",
    "TradeLog",
    "Outcome",
    "compute_metrics",
    "limit_pct_for",
    "compute_ref_prev_close",
    "is_one_word",
    "_action_verdict",
    # P2
    "PitReader",
    "NewsItem",
    "EventItem",
    "VisibleInfo",
    "select_candidates",
    "CandidateConfig",
    "CandidateResult",
    "DecisionAgent",
    "Decision",
    "DecisionInput",
    "DecisionLeg",
    "DecisionCache",
    "BacktestRunner",
    "RunConfig",
    "BacktestResult",
]
