"""P5 — buddy 端 BacktestRunner 适配: 构造 reader/agent/runner, 跑回测,
把 BacktestResult 映射成 quant.jsx 吃的 _jsonable dict。

PIT 纪律: 全程经 P2 的 PitReader + RunConfig (已 PIT-safe), 不另开数据通道,
不调 news-reader/f10-reader/factor-computer。mock 模式注入确定性 stub agent
(0 次 LLM, 不依赖 DASHSCOPE key)。

注意: body['decisions'] 透传 decisions_by_date (real 模式是未清洗的 LLM raw),
安全性依赖最外层 return _jsonable(body) 递归洗 NaN/Inf — 不要在 _jsonable 之前
提前 return decisions 子结构而漏洗。
"""
from __future__ import annotations
from dataclasses import asdict
from typing import List, Optional, Tuple

from financial_analyst.buddy.server import _jsonable
from financial_analyst.backtest.decision import (
    Decision, DecisionLeg, DecisionAgent, DecisionCache)
from financial_analyst.backtest.candidate import CandidateConfig
from financial_analyst.backtest.engine import BacktestRunner, RunConfig


class _MockAgent:
    """确定性盘前决策 (0 次 LLM, 不依赖 DASHSCOPE key):

    决策优先级 (高 → 低):
      1. 持仓收益 ≥ take_profit_pct → sell (止盈)
      2. 持仓收益 ≤ -stop_loss_pct  → sell (止损)
      3. 持有 ≥ hold_days           → sell (定时了结)
      4. 空仓 + 有候选              → 对 rev20 分位最低 (跌最多) 的 1 只 buy 50%
      5. 否则                       → hold

    raw 写成结构化 dict (含 decisions 列表), 以便前端理由回退能取到。
    """
    def __init__(self, hold_days: int = 3,
                 take_profit_pct: Optional[float] = None,
                 stop_loss_pct: Optional[float] = None):
        self._n = 0
        self._hold_days = hold_days
        self._take_profit_pct = take_profit_pct
        self._stop_loss_pct = stop_loss_pct
        self._held_since: dict = {}   # code -> 已持有的决策日计数

    @property
    def n_calls(self):
        return self._n

    async def decide(self, inp) -> Decision:
        legs: List[DecisionLeg] = []
        holdings = inp.holdings or {}
        unrealized = getattr(inp, "unrealized_pct", {}) or {}

        # 推进持有计数
        for code in list(self._held_since):
            if code in holdings:
                self._held_since[code] += 1
            else:
                self._held_since.pop(code, None)

        # 优先级 1+2+3: 检查所有持仓的止盈/止损/到期
        for code in list(holdings):
            pct = unrealized.get(code, 0.0)
            if self._take_profit_pct is not None and pct >= self._take_profit_pct:
                legs.append(DecisionLeg(code=code, action="sell",
                    reason=f"mock: 止盈 (收益 {pct:+.1%} ≥ {self._take_profit_pct:.1%})"))
                continue
            if self._stop_loss_pct is not None and pct <= -self._stop_loss_pct:
                legs.append(DecisionLeg(code=code, action="sell",
                    reason=f"mock: 止损 (收益 {pct:+.1%} ≤ -{self._stop_loss_pct:.1%})"))
                continue
            n = self._held_since.get(code, 0)
            if n >= self._hold_days:
                legs.append(DecisionLeg(code=code, action="sell",
                    reason=f"mock: 持有满 {n} 日 (阈值 {self._hold_days}), 了结"))

        # 优先级 4: 空仓 + 有候选 + 本轮没产 sell → buy 最低 rev20
        if not holdings and inp.candidates and not legs:
            top = sorted(inp.candidates,
                         key=lambda c: inp.rev20_rank.get(c, 1.0))[0]
            legs = [DecisionLeg(code=top, action="buy", weight_pct=50.0,
                                stop_loss=0.0,
                                reason=f"mock: rev20 分位最低({top}), 反转介入")]
            self._held_since[top] = 0

        raw = {"market_view": "mock 决策(确定性)",
               "decisions": [asdict(l) for l in legs], "warnings": []}
        return Decision(market_view=raw["market_view"], decisions=legs,
                        warnings=[], raw=raw)


def _fills_to_trades(trade_log) -> List[dict]:
    out = []
    for f in trade_log.fills:
        out.append({
            "date": f.trade_date, "action": f.side, "code": f.code,
            "price": f.price, "qty": f.qty,
            "pnl": f.realized_pnl if f.side == "sell" else 0.0,
            "reason": f.reason or "",          # broker 恒置空, UI 用 decisions 回退
        })
    return out


def _nav_to_series(nav_history: List[Tuple[str, float]], init_cash: float):
    """nav_history 是 levels(元); 归一化为首=1.0 一维数组 + dates。"""
    if not nav_history:
        return [], []
    base = nav_history[0][1] or init_cash
    series = [round(lv / base, 6) for _, lv in nav_history]
    dates = [d for d, _ in nav_history]
    return series, dates


def _default_window(reader, start: Optional[str], end: Optional[str]):
    """start=None → data_end 前 ~10 个交易日; 不硬编码任何固定日期。"""
    de = str(reader.data_end().date())
    if start:
        return start, end
    days = reader.trading_days("1990-01-01", de)
    s = days[-10] if len(days) >= 10 else (days[0] if days else de)
    return s, (end or de)


async def run_backtest(req) -> dict:
    from financial_analyst.backtest.pit_reader import PitReader
    reader = PitReader()                       # 自动经 get_data_paths() 解析
    start, end = _default_window(reader, req.start, req.end)

    if req.mode == "mock":
        agent = _MockAgent(
            hold_days=req.hold_days,
            take_profit_pct=req.take_profit_pct,
            stop_loss_pct=req.stop_loss_pct,
        )
        cache_dir = None
    else:
        cache_dir = None                       # real 可选挂 DecisionCache 断点续跑
        agent = DecisionAgent(client=None,     # 懒初始化 LLMClient.for_agent
                              cache=DecisionCache(cache_dir) if cache_dir else None)

    cfg = RunConfig(
        start=start, end=end, init_cash=req.init_cash,
        benchmark=None, match_freq=req.match_freq,
        candidate=CandidateConfig(
            topn=req.candidate_topn,
            pool=req.pool,        # P2: 池子模式
        ),
        cache_dir=cache_dir)
    runner = BacktestRunner(reader=reader, agent=agent, cfg=cfg)
    result = await runner.run()

    pr = result.portfolio_result
    series, dates = _nav_to_series(result.nav_history, req.init_cash)
    bench = None
    if result.benchmark_nav:
        bbase = result.benchmark_nav[0][1] or req.init_cash
        bench = [round(v / bbase, 6) for _, v in result.benchmark_nav]
        if len(bench) != len(series):
            bench = None                       # 长度不齐 → 前端忽略, 这里也清掉

    warnings = list(result.warnings)
    if len(series) < 2:
        warnings.append("窗口过短, 净值点 < 2, 无法绘曲线 (请选 ≥2 个交易日)")

    ts = result.trade_stats
    kpi = {
        "ann_return": pr.ann_return, "sharpe": pr.sharpe,
        "max_drawdown": pr.max_drawdown, "volatility": pr.volatility,
        "turnover": pr.turnover, "win_rate": pr.win_rate, "calmar": pr.calmar,
        "trade_win_rate": ts.get("trade_win_rate"),
        "profit_factor": ts.get("profit_factor"),   # 可能 inf → _jsonable 转 null
        "n_trades": ts.get("n_trades"),
        "n_llm_calls": result.n_llm_calls,
    }
    body = {
        "mode": req.mode,
        "params": {**req.model_dump(), "start": start, "end": end},  # 回填实际窗口
        "nav": {"series": series, "dates": dates},
        "benchmark": bench,
        "kpi": kpi,
        "trades": _fills_to_trades(result.trade_log),
        "decisions": result.decisions_by_date,
        "warnings": warnings,
        # P1.3: 末日 CandidateResult.filter_stats → 前端 PoolFilterPopover 显示真数字
        "candidate_filter_stats": result.candidate_filter_stats,
    }
    return _jsonable(body)     # NaN/Inf→null, numpy 标量→native (最外层统一洗)
