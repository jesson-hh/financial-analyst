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


# mock 定期卖出窗口: 买入后持有这么多个"决策日"再无条件卖出 (T+1 已满足, 演示
# 能跑出真实 buy→sell 回合 + 非空 trade_stats)。stop_loss 仍传 0 (不触发 broker
# EOD stop), 卖出完全由这条规则驱动 → 可手算核对、确定性。
_MOCK_HOLD_DAYS = 3


class _MockAgent:
    """确定性盘前决策 (0 次 LLM, 不依赖 DASHSCOPE key):
      * 空仓 → 对候选池里 rev20 分位最低(跌最多)的 1 只挂 buy(反转逻辑);
      * 已持有且持有满 _MOCK_HOLD_DAYS 个决策日 → 卖出(演示一个完整回合);
      * 否则 hold。
    raw 写成结构化 dict (含 decisions 列表), 以便前端理由回退能取到。"""
    def __init__(self):
        self._n = 0
        self._held_since: dict = {}   # code -> 已持有的决策日计数

    @property
    def n_calls(self):
        return self._n

    async def decide(self, inp) -> Decision:
        legs: List[DecisionLeg] = []
        holdings = inp.holdings or {}
        # 推进持有计数
        for code in list(self._held_since):
            if code in holdings:
                self._held_since[code] += 1
            else:
                self._held_since.pop(code, None)

        if not holdings and inp.candidates:
            top = sorted(inp.candidates,
                         key=lambda c: inp.rev20_rank.get(c, 1.0))[0]
            legs = [DecisionLeg(code=top, action="buy", weight_pct=50.0,
                                stop_loss=0.0,
                                reason=f"mock: rev20 分位最低({top}), 反转介入")]
            self._held_since[top] = 0
        else:
            for code, n in list(self._held_since.items()):
                if n >= _MOCK_HOLD_DAYS and code in holdings:
                    legs.append(DecisionLeg(code=code, action="sell",
                                            reason=f"mock: 持有满 {n} 日, 了结"))
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
        agent = _MockAgent()
        cache_dir = None
    else:
        cache_dir = None                       # real 可选挂 DecisionCache 断点续跑
        agent = DecisionAgent(client=None,     # 懒初始化 LLMClient.for_agent
                              cache=DecisionCache(cache_dir) if cache_dir else None)

    cfg = RunConfig(
        start=start, end=end, init_cash=req.init_cash,
        benchmark=None, match_freq=req.match_freq,
        candidate=CandidateConfig(topn=req.candidate_topn),
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
    }
    return _jsonable(body)     # NaN/Inf→null, numpy 标量→native (最外层统一洗)
