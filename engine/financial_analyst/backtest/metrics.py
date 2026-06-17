"""compute_metrics — VirtualPortfolio NAV levels → fa PortfolioResult.

Reuses ``financial_analyst.factors.eval.portfolio.portfolio_stats`` (pure
numpy/pandas, no LLM, no network) rather than re-deriving annualization; only
borrows ann_return / volatility / sharpe / win_rate from it. Max-drawdown and
calmar are recomputed directly off the normalized NAV (authoritative), because
``portfolio_stats`` rebuilds NAV from the surviving returns and can lose the
initial peak.

Two corrections baked in vs a naive bridge (see P1 spec §0.2):

* ``pct_change().fillna(0.0)`` — NOT ``.dropna()``. dropna drops the
  synthetic-init→day1 return and the initial peak, which zeroes max-drawdown.
* short-window guard: with fewer than 3 NAV points the geometric annualization
  explodes (a single 5% period → ann_return ≈ 218625), so ann_return / sharpe /
  calmar are forced to NaN. Short windows have no statistical meaning here.

Precondition: ``nav_history[0]`` must be the synthetic init point
(nav == init_cash); the caller seeds it via ``VirtualPortfolio.seed_initial_nav``.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd

from financial_analyst.factors.eval.portfolio import PortfolioResult, portfolio_stats

_NAN = float("nan")


def compute_metrics(
    nav_history: List[Tuple[str, float]],
    init_cash: float,
    turnover: float = _NAN,
    benchmark_nav: Optional[List[Tuple[str, float]]] = None,
    trade_win_rate: float = _NAN,
    ppy: int = 252,
) -> PortfolioResult:
    """Bridge NAV levels to a ``PortfolioResult``.

    Notes
    -----
    * ``win_rate`` keeps the fa long-short convention = **fraction of up days**
      (flat / empty days count as non-wins), NOT per-trade win rate. The
      per-trade win rate lives on ``TradeLog.trade_stats()`` and is accepted
      here via ``trade_win_rate`` but deliberately NOT mixed into
      ``PortfolioResult.win_rate`` (which has no such field) — the front end
      shows the two under separate labels.
    * Short windows (< ~ a meaningful length) have no annualization meaning.
    """
    if not nav_history:
        return PortfolioResult(turnover=turnover, benchmark_nav=benchmark_nav)

    levels = [lv for _, lv in nav_history]
    nv = pd.Series([lv / init_cash for lv in levels], dtype=float)

    ls = nv.pct_change().fillna(0.0)
    st = portfolio_stats(ls, ppy)

    # authoritative max-drawdown / calmar from normalized NAV
    mdd = float((nv / nv.cummax() - 1).min())
    ann = st["ann_return"]
    calmar = float(ann / abs(mdd)) if (mdd < 0 and ann == ann) else _NAN

    ann_return = ann
    sharpe = st["sharpe"]
    # short-window guard: < 3 NAV points → annualized stats are meaningless
    if len(nv) < 3:
        ann_return = _NAN
        sharpe = _NAN
        calmar = _NAN

    nav_series: List[Tuple[str, float]] = [
        (ts, float(v)) for (ts, _), v in zip(nav_history, nv.tolist())
    ]

    return PortfolioResult(
        nav_series=nav_series,
        benchmark_nav=benchmark_nav,
        ann_return=ann_return,
        sharpe=sharpe,
        max_drawdown=mdd,
        volatility=st["volatility"],
        turnover=turnover,
        win_rate=st["win_rate"],
        calmar=calmar,
    )
