"""多空组合: 多 top 组 / 空 bottom 组等权, 按调仓日算净值 + 年化/Sharpe/回撤/换手/胜率。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from financial_analyst.factors.eval.quantile import _assign_groups


@dataclass
class PortfolioResult:
    nav_series: List[Tuple[str, float]] = field(default_factory=list)
    benchmark_nav: Optional[List[Tuple[str, float]]] = None
    ann_return: float = float("nan")
    sharpe: float = float("nan")
    max_drawdown: float = float("nan")
    volatility: float = float("nan")
    turnover: float = float("nan")
    win_rate: float = float("nan")
    calmar: float = float("nan")


def portfolio_stats(ls: pd.Series, ppy: int) -> Dict[str, float]:
    """Annualized stats from a per-period long-short return series (chronological).

    Sharpe uses risk-free = 0 (a dollar-neutral long-short book is self-financing).
    ann_return is geometric and NaN if the cumulative NAV goes non-positive.
    """
    ls = ls.dropna()
    n = len(ls)
    nan = float("nan")
    if n == 0:
        return {"ann_return": nan, "volatility": nan, "sharpe": nan,
                "max_drawdown": nan, "calmar": nan, "win_rate": nan}
    nav = (1 + ls).cumprod()
    navend = float(nav.iloc[-1])
    ann = navend ** (ppy / n) - 1 if navend > 0 else nan
    vol = float(ls.std() * np.sqrt(ppy)) if n > 1 else 0.0
    sharpe = float(ls.mean() * ppy / vol) if vol and vol > 0 else nan
    mdd = float((nav / nav.cummax() - 1).min())
    calmar = float(ann / abs(mdd)) if (mdd < 0 and ann == ann) else nan
    win = float((ls > 0).mean())
    return {"ann_return": float(ann) if ann == ann else nan, "volatility": vol, "sharpe": sharpe,
            "max_drawdown": mdd, "calmar": calmar, "win_rate": win}


def long_short_portfolio(alpha: pd.Series, fwd: pd.Series,
                         n_groups: int = 10, ppy: int = 12,
                         cost_bps: float = 0.0) -> PortfolioResult:
    """Long the top group / short the bottom group (equal weight), per rebalance date.

    Transaction cost model: ``cost_bps`` is charged on one-sided top-group turnover
    and multiplied by 2 to proxy the (assumed-symmetric) short leg. The reported
    ``turnover`` is the average one-sided top-group symmetric turnover in [0,1].
    """
    joined = pd.concat([alpha.rename("a"), fwd.rename("f")], axis=1).dropna()
    if joined.empty:
        return PortfolioResult()
    joined["g"] = _assign_groups(joined["a"], n_groups)
    joined = joined.dropna(subset=["g"])
    if joined.empty:
        return PortfolioResult()

    dates = sorted(joined.index.get_level_values("datetime").unique())
    ls_vals: List[float] = []
    turns: List[float] = []
    prev_top: set = set()
    used_dates: List = []
    for d in dates:
        sl = joined.xs(d, level="datetime")
        gmax = sl["g"].max()
        top = sl[sl["g"] == gmax]
        bot = sl[sl["g"] == 0]
        if len(top) == 0 or len(bot) == 0:
            continue
        gross = float(top["f"].mean() - bot["f"].mean())
        top_codes = set(top.index)
        # symmetric top-group turnover, normalized by combined size → bounded [0,1];
        # first rebalance has no prior holdings (no entry cost charged).
        if prev_top:
            denom = len(top_codes) + len(prev_top)
            turn = len(top_codes ^ prev_top) / denom if denom else 0.0
        else:
            turn = 0.0
        net = gross - turn * (cost_bps / 1e4) * 2
        ls_vals.append(net)
        turns.append(turn)
        used_dates.append(d)
        prev_top = top_codes

    ls = pd.Series(ls_vals, index=pd.Index(used_dates, name="datetime"))
    st = portfolio_stats(ls, ppy)
    nav = (1 + ls).cumprod()
    nav_series = [(str(pd.Timestamp(d).date()), float(v)) for d, v in nav.items()]
    return PortfolioResult(
        nav_series=nav_series, benchmark_nav=None,
        ann_return=st["ann_return"], sharpe=st["sharpe"],
        max_drawdown=st["max_drawdown"], volatility=st["volatility"],
        turnover=float(np.mean(turns)) if turns else float("nan"),
        win_rate=st["win_rate"], calmar=st["calmar"],
    )
