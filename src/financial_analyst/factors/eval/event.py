"""事件信号研究 (SP-B.2) — 把触发型因子当事件做 event study。

截面 IC/十分位对稀疏布尔触发是错口径; 这里统计每次触发后 horizon 日的前向收益
(原始 + 市场调整 excess)、CAR 曲线、逐年稳定性。build_event_report 纯 (合成面板
可单测); event_report 做 I/O。永不抛, 结构化错误态。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, List, Optional, Tuple

import pandas as pd

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.report import forward_simple_returns

_PRIMARY_H = 5  # by_year 用的主 horizon


@dataclass
class EventHorizon:
    h: int
    n: int
    mean_ret: float = float("nan")
    mean_excess: float = float("nan")
    win_rate: float = float("nan")
    t_stat: float = float("nan")


@dataclass
class EventReport:
    factor: str
    universe: str
    start: str
    end: str
    n_dates: int
    n_codes: int
    n_events: int
    event_rate: float = float("nan")
    horizons: List[EventHorizon] = field(default_factory=list)
    car_curve: List[Tuple[int, float]] = field(default_factory=list)
    by_year: List[Tuple[str, int, float]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


def _excess(fwd: pd.Series) -> pd.Series:
    """逐日减等权全市场前向收益 → 市场调整 (abnormal)。"""
    mkt = fwd.groupby(level="datetime").transform("mean")
    return fwd - mkt


def _stats(raw: pd.Series, exc: pd.Series):
    n = int(raw.shape[0])
    if n == 0:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    mean_ret, mean_exc, win = float(raw.mean()), float(exc.mean()), float((raw > 0).mean())
    if n >= 2:
        sd = float(exc.std(ddof=1))
        t = mean_exc / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    else:
        t = float("nan")
    return n, mean_ret, mean_exc, win, t


def build_event_report(panel, compute: Callable, config: EvalConfig,
                       factor_label: str = "", horizons=(1, 5, 10, 20)) -> EventReport:
    dates = list(panel.dates())
    rpt = EventReport(
        factor=factor_label, universe=config.universe,
        start=str(pd.Timestamp(min(dates)).date()) if dates else (config.start or ""),
        end=str(pd.Timestamp(max(dates)).date()) if dates else (config.end or ""),
        n_dates=len(dates), n_codes=panel.n_codes(), n_events=0,
    )
    try:
        sig = compute(panel)
    except Exception as e:
        rpt.status, rpt.error = "compute_error", f"{type(e).__name__}: {e}"
        return rpt
    if not isinstance(sig, pd.Series):
        rpt.status, rpt.error = "compute_error", f"compute 返回 {type(sig).__name__}, 需 pd.Series"
        return rpt

    valid = sig.astype(float).dropna()
    fired = valid[valid > 0]
    rpt.n_events = int(fired.shape[0])
    rpt.event_rate = float(fired.shape[0] / valid.shape[0]) if valid.shape[0] else float("nan")
    if rpt.n_events == 0:
        rpt.status, rpt.error = "no_events", "触发表达式从未 firing (信号恒 ≤0/NaN)。"
        return rpt
    if rpt.event_rate > 0.5:
        rpt.warnings.append(
            f"事件率 {rpt.event_rate:.0%} 偏高 — 这更像连续因子而非事件触发, 截面评测请用 factor_report。")

    ev_idx = fired.index
    max_h = max(horizons)
    for d in range(1, max_h + 1):
        e = _excess(forward_simple_returns(panel, d)).reindex(ev_idx).dropna()
        if e.shape[0] > 0:
            rpt.car_curve.append((d, float(e.mean())))

    for h in horizons:
        fwd = forward_simple_returns(panel, h)
        exc = _excess(fwd)
        sub = pd.DataFrame({"raw": fwd.reindex(ev_idx), "exc": exc.reindex(ev_idx)}).dropna()
        n, mr, me, win, t = _stats(sub["raw"], sub["exc"])
        rpt.horizons.append(EventHorizon(h=h, n=n, mean_ret=mr, mean_excess=me, win_rate=win, t_stat=t))

    exc_p = _excess(forward_simple_returns(panel, _PRIMARY_H)).reindex(ev_idx).dropna()
    if exc_p.shape[0] > 0:
        yrs = exc_p.index.get_level_values("datetime").year
        by = pd.Series(exc_p.values, index=yrs)
        for y, g in by.groupby(level=0):
            rpt.by_year.append((str(int(y)), int(g.shape[0]), float(g.mean())))

    if rpt.n_events < 30:
        rpt.warnings.append(f"事件样本少 ({rpt.n_events} < 30), 结论不稳健。")
    rpt.warnings.append("overlapping 事件 t 值可能膨胀 (v1 未做去重/Newey-West)。")
    return rpt


def event_report(spec_or_expr: str, config: Optional[EvalConfig] = None,
                 horizons=(1, 5, 10, 20)) -> EventReport:
    """I/O 编排: universe → 加载日频面板 → 取触发(注册名或表达式) → build_event_report。"""
    config = config or EvalConfig()
    from financial_analyst.data.universe import resolve_universe_codes
    codes = resolve_universe_codes(config.universe)
    if not codes:
        return EventReport(spec_or_expr, config.universe, config.start or "", config.end or "",
                           0, 0, 0, status="empty_universe",
                           error=f"universe '{config.universe}' 解析为空 (试 fa data bootstrap 或换 csi300_active)。")
    end = config.end or date.today().isoformat()
    start = config.start or (date.today() - timedelta(days=365 * 2)).isoformat()
    from financial_analyst.factors.zoo.panel import PanelData
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind = IndustryLoader() if industry_map_path().exists() else None
        except Exception:
            ind = None
        panel = PanelData.from_loader(loader, codes, start, end, freq="day", industry_loader=ind)
    except Exception as e:
        return EventReport(spec_or_expr, config.universe, start, end, 0, len(codes), 0,
                           status="load_error", error=f"{type(e).__name__}: {e}")
    from financial_analyst.factors.zoo.registry import get as _get_alpha
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
    try:
        compute, label = _get_alpha(spec_or_expr).compute, spec_or_expr
    except KeyError:
        validate_expr(spec_or_expr)
        compute, label = compile_factor(spec_or_expr), spec_or_expr
    return build_event_report(panel, compute, config, factor_label=label, horizons=horizons)
