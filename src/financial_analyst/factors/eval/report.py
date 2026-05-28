"""报告编排: FactorReport 组装 + 因子特征 + build_report(纯) + factor_report(I/O)。"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.eval.config import EvalConfig
from financial_analyst.factors.eval.preprocess import winsorize, zscore
from financial_analyst.factors.eval.ic import ic_analysis, IcResult
from financial_analyst.factors.eval.quantile import quantile_backtest, QuantileResult
from financial_analyst.factors.eval.portfolio import long_short_portfolio, PortfolioResult


@dataclass
class ReportMeta:
    factor: str
    family: str
    universe: str
    freq: str
    start: str
    end: str
    n_dates: int
    n_codes: int
    fwd_days: int
    preprocess: dict = field(default_factory=dict)


@dataclass
class FactorChar:
    coverage: float = float("nan")
    autocorr_1: float = float("nan")
    half_life: float = -1.0
    top_group_turnover: float = float("nan")


@dataclass
class FactorReport:
    meta: ReportMeta
    ic: Optional[IcResult] = None
    quantile: Optional[QuantileResult] = None
    portfolio: Optional[PortfolioResult] = None
    characteristics: FactorChar = field(default_factory=FactorChar)
    warnings: List[str] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


def rebalance_dates(all_dates: List, freq: str) -> List:
    """Resample a daily date list to rebalance dates: the last observed (trading) day in the panel for each week/month period (freq='day' returns all dates)."""
    s = pd.Series(1, index=pd.DatetimeIndex(sorted(pd.to_datetime(all_dates))))
    if freq == "day":
        return list(s.index)
    period = {"week": "W", "month": "M"}.get(freq, "M")
    last = s.groupby(s.index.to_period(period)).apply(lambda g: g.index.max())
    return list(pd.DatetimeIndex(last.values))


def forward_simple_returns(panel, n: int) -> pd.Series:
    """Simple n-day forward return per code: close(t+n)/close(t) - 1."""
    close = panel.close
    fwd_close = close.groupby(level="code", group_keys=False).shift(-n)
    return fwd_close / close - 1.0


def _restrict(s: pd.Series, dates) -> pd.Series:
    keep = pd.DatetimeIndex(dates)
    return s[s.index.get_level_values("datetime").isin(keep)]


def _benchmark_nav(fwd_r: pd.Series) -> List:
    """Equal-weight universe nav from per-date mean forward return."""
    by = fwd_r.groupby(level="datetime").mean().dropna().sort_index()
    nav = (1 + by).cumprod()
    return [(str(pd.Timestamp(d).date()), float(v)) for d, v in nav.items()]


def factor_characteristics(alpha: pd.Series, n_codes: int) -> FactorChar:
    a = alpha.dropna()
    if a.empty or n_codes <= 0:
        return FactorChar()
    per_date_cov = a.groupby(level="datetime").size() / float(n_codes)
    coverage = float(per_date_cov.mean())

    dates = sorted(a.index.get_level_values("datetime").unique())

    def _xs_autocorr(lag: int) -> float:
        vals = []
        for i in range(lag, len(dates)):
            cur = a.xs(dates[i], level="datetime")
            prev = a.xs(dates[i - lag], level="datetime")
            common = cur.index.intersection(prev.index)
            if len(common) < 3:
                continue
            c = cur.loc[common].corr(prev.loc[common], method="spearman")
            if c == c:
                vals.append(c)
        return float(np.mean(vals)) if vals else float("nan")

    half_life = -1.0
    autocorr_1 = float("nan")
    for lag in (1, 2, 3, 5, 8, 13, 21):
        ac = _xs_autocorr(lag)
        if lag == 1:
            autocorr_1 = ac
        if ac == ac and ac < 0.5:
            half_life = float(lag)
            break
    return FactorChar(coverage=coverage, autocorr_1=autocorr_1, half_life=half_life)


def build_report(panel, compute: Callable, config: EvalConfig,
                 factor_label: str, family: str) -> FactorReport:
    fwd_days = config.effective_fwd_days()
    meta = ReportMeta(
        factor=factor_label, family=family, universe=config.universe,
        freq=config.freq,
        start=str(pd.Timestamp(panel.dates().min()).date()),
        end=str(pd.Timestamp(panel.dates().max()).date()),
        n_dates=0, n_codes=panel.n_codes(), fwd_days=fwd_days,
        preprocess={"winsorize_q": config.winsorize_q,
                    "standardize": config.standardize,
                    "neutralize": False},
    )
    warnings: List[str] = []
    if config.neutralize:
        warnings.append("中性化 (neutralize=True) 暂未实现 (SP-A.2), 已跳过。")

    try:
        alpha = compute(panel)
    except Exception as e:
        return FactorReport(meta, status="compute_error", error=f"{type(e).__name__}: {e}",
                            warnings=warnings)
    if not isinstance(alpha, pd.Series):
        return FactorReport(meta, status="bad_output",
                            error=f"compute returned {type(alpha).__name__}, expected pd.Series",
                            warnings=warnings)

    if config.winsorize_q and config.winsorize_q > 0:
        alpha = winsorize(alpha, config.winsorize_q)
    if config.standardize:
        alpha = zscore(alpha)

    reb = rebalance_dates(list(panel.dates()), config.freq)
    fwd = forward_simple_returns(panel, fwd_days)
    alpha_r = _restrict(alpha, reb)
    fwd_r = _restrict(fwd, reb)
    meta.n_dates = len(pd.Index(alpha_r.dropna().index.get_level_values("datetime")).unique())

    fwd_by_h = {h: _restrict(forward_simple_returns(panel, h), reb) for h in config.decay_horizons}
    ic = ic_analysis(alpha_r, fwd_r, fwd_by_h)
    q = quantile_backtest(alpha_r, fwd_r, config.n_groups, config.periods_per_year())
    pf = long_short_portfolio(alpha_r, fwd_r, config.n_groups, config.periods_per_year(), config.cost_bps)
    pf.benchmark_nav = _benchmark_nav(fwd_r)
    ch = factor_characteristics(alpha_r, panel.n_codes())
    ch.top_group_turnover = pf.turnover

    if meta.n_dates < 12:
        warnings.append(f"样本太短 (有效调仓期 {meta.n_dates} < 12), 结论不稳健。")
    if ch.coverage == ch.coverage and ch.coverage < 0.5:
        warnings.append(f"因子覆盖率低 ({ch.coverage:.0%}).")
    if ic.rank_ic_mean == ic.rank_ic_mean and ic.rank_ic_mean < 0:
        warnings.append("RankIC 为负 — 因子方向为反向 (高分→未来跌)。")

    return FactorReport(meta, ic, q, pf, ch, warnings, "ok", "")


def factor_report(spec_or_expr: str, config: Optional[EvalConfig] = None) -> FactorReport:
    """I/O 编排: 解析 universe → 加载日频面板 → 取因子(注册名或表达式) → build_report。"""
    config = config or EvalConfig()
    from financial_analyst.data.universe import resolve_universe_codes

    codes = resolve_universe_codes(config.universe)
    if not codes:
        empty_meta = ReportMeta(spec_or_expr, "?", config.universe, config.freq,
                                config.start or "", config.end or "", 0, 0,
                                config.effective_fwd_days())
        return FactorReport(empty_meta, status="empty_universe",
                            error=f"universe '{config.universe}' 解析为空 (试 fa data bootstrap 或换 csi300_active)。")

    end = config.end or date.today().isoformat()
    start = config.start or (date.today() - timedelta(days=365 * 2)).isoformat()

    from financial_analyst.factors.zoo.panel import PanelData
    try:
        from financial_analyst.data.loader_factory import get_default_loader
        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:
            ind_loader = None
        panel = PanelData.from_loader(loader, codes, start, end, freq="day", industry_loader=ind_loader)
    except Exception as e:
        load_meta = ReportMeta(spec_or_expr, "?", config.universe, config.freq,
                               start, end, 0, len(codes), config.effective_fwd_days())
        return FactorReport(load_meta, status="load_error", error=f"{type(e).__name__}: {e}")

    from financial_analyst.factors.zoo.registry import get as _get_alpha
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
    try:
        spec = _get_alpha(spec_or_expr)
        compute, family, label = spec.compute, spec.family, spec_or_expr
    except KeyError:
        validate_expr(spec_or_expr)
        compute, family, label = compile_factor(spec_or_expr), "custom", spec_or_expr
    return build_report(panel, compute, config, label, family)
