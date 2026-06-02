"""SP-D 多因子合成编排 (Factor Composite Model).

把 N (>=2) 个成员因子 (注册 alpha 名或白名单表达式) 合成一个综合打分, 在
**样本外 (OOS)** 用 SP-A 的 build_report 评测综合分, 并对各成员同窗 OOS 指标
做对比 → 回答"合成是否增益"。

OOS 纪律: 调仓日按时间排序, 前 ``train_frac`` 为训练段 (拟合权重), 余为测试段。
综合分 Series 只在测试调仓行有值 (训练行 NaN) → build_report 的 dropna 自然
只评 OOS。成员对比也只在测试调仓行评测, 与综合分同窗可比。

已知简化 (v1): 训练/测试边界**无 purge/embargo gap** —— 最后一个训练调仓日的
``close(t+fwd)/close(t)-1`` 前瞻标签会越界进入测试期 (López de Prado purging 关注点)。
这不破坏无偷看 (拟合从不索引测试行的 fwd; 对抗式扰动测试 fwd 后权重不变), 且
build_report 同此约定故综合分-成员对比仍同窗可比。严格 purging + walk-forward 多窗
留作后续增强 (见 spec "不做" 段)。

本模块**绝不抛异常** —— 所有失败 (输入非法 / universe 空 / 加载失败 / 拟合失败)
都归为结构化的 ``status`` + ``error``, 仿 ``factor_report`` 的 I/O 编排风格。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from math import floor
from typing import Dict, List, Optional, Tuple

import pandas as pd

from financial_analyst.factors.compose.combine import combine
from financial_analyst.factors.compose.matrix import build_factor_matrix
from financial_analyst.factors.eval import EvalConfig, FactorReport
from financial_analyst.factors.eval.report import (
    build_report,
    forward_simple_returns,
    rebalance_dates,
)


@dataclass
class MemberOOS:
    """单个成员因子在测试段 (OOS) 的指标。"""

    name: str
    rank_ic: float
    sharpe: float


@dataclass
class ComposeResult:
    """合成结果 (结构化, 永不抛)。

    status: ok / too_few_factors / empty_universe / load_error / fit_error。
    composite: 综合分的 OOS FactorReport (失败时为 None)。
    """

    method: str
    members: list
    weights: dict
    train_frac: float
    n_train_dates: int
    n_test_dates: int
    composite: object = None  # FactorReport or None
    member_oos: list = field(default_factory=list)
    verdict: str = ""
    warnings: list = field(default_factory=list)
    status: str = "ok"
    error: str = ""
    # SP-3 SHAP: 仅 method='lgbm' 时填充. 形如
    # {code: [(factor, signed_contrib), ...]} 按 |contrib| 降序取 top-5.
    # 测试集最后一日的横截面 (一只股一行) 上算 SHAP, 解释 LGB 当前的因子贡献.
    composite_shap_top5: Optional[Dict[str, List[Tuple[str, float]]]] = field(default=None)


def _resolve_member_compute(name: str):
    """成员名 → compute 函数: 先试注册 alpha 名, 失败回退为白名单表达式。

    解析失败抛 Exception (由调用方守卫)。"""
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
    from financial_analyst.factors.zoo.registry import get as reg_get

    try:
        return reg_get(name).compute
    except KeyError:
        validate_expr(name)
        return compile_factor(name)


def _safe_float(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def compose_factors(
    members: list,
    config: EvalConfig | None = None,
    method: str = "lgbm",
    train_frac: float = 0.6,
) -> ComposeResult:
    """合成 N 个成员因子, 在 OOS 测试段评测综合分并对比成员。

    Parameters
    ----------
    members : list[str]
        成员因子名或白名单表达式, 至少 2 个。
    config : EvalConfig | None
        评测配置 (universe / freq / 窗口 / n_groups / 预处理)。None → 默认。
    method : str
        equal / ic_weighted / linear / lgbm。
    train_frac : float
        训练段调仓日占比 (前段拟合, 余段测试)。

    Returns
    -------
    ComposeResult — 永不抛异常, 失败模式由 status/error 表达。
    """
    config = config or EvalConfig()
    warnings: list = []

    # 1) 成员数守卫。
    if len(members) < 2:
        return ComposeResult(
            method=method,
            members=list(members),
            weights={},
            train_frac=train_frac,
            n_train_dates=0,
            n_test_dates=0,
            status="too_few_factors",
            error="合成至少需要 2 个因子",
        )

    # 2) 解析 universe + 加载面板 (仿 factor_report 的本地 import, 便于 monkeypatch)。
    from financial_analyst.data.universe import resolve_universe_codes

    codes = resolve_universe_codes(config.universe)
    if not codes:
        return ComposeResult(
            method=method,
            members=list(members),
            weights={},
            train_frac=train_frac,
            n_train_dates=0,
            n_test_dates=0,
            status="empty_universe",
            error=f"universe '{config.universe}' 解析为空 (试 fa data bootstrap 或换 csi300_active)。",
        )

    end = config.end or date.today().isoformat()
    start = config.start or (date.today() - timedelta(days=365 * 2)).isoformat()

    from financial_analyst.factors.zoo.panel import PanelData

    try:
        from financial_analyst.data.loader_factory import get_default_loader

        loader = get_default_loader()
        try:
            from financial_analyst.data.loaders.industry import (
                IndustryLoader,
                industry_map_path,
            )

            ind_loader = IndustryLoader() if industry_map_path().exists() else None
        except Exception:
            ind_loader = None
        from financial_analyst.factors.zoo.panel_cache import load_panel_cached
        panel = load_panel_cached(
            loader, codes, start, end, freq="day", industry_loader=ind_loader
        )
    except Exception as e:
        return ComposeResult(
            method=method,
            members=list(members),
            weights={},
            train_frac=train_frac,
            n_train_dates=0,
            n_test_dates=0,
            status="load_error",
            error=f"{type(e).__name__}: {e}",
        )

    # 3) 因子矩阵 (失败成员被跳过, 记 warning)。
    matrix, names = build_factor_matrix(panel, members)
    skipped = [m for m in members if m not in names]
    if skipped:
        warnings.append(f"{len(skipped)} 个成员无法计算被跳过: {skipped}")
    if len(names) < 2:
        return ComposeResult(
            method=method,
            members=list(members),
            weights={},
            train_frac=train_frac,
            n_train_dates=0,
            n_test_dates=0,
            warnings=warnings,
            status="too_few_factors",
            error="有效成员不足 2 个 (其余无法计算)。",
        )

    # 4) 前瞻收益。
    fwd_days = config.effective_fwd_days()
    fwd = forward_simple_returns(panel, fwd_days)

    # 5) 调仓日 → 限制矩阵/fwd 到调仓行 → 按 train_frac 时间切 train/test。
    reb = sorted(pd.DatetimeIndex(rebalance_dates(list(panel.dates()), config.freq)))
    reb_set = set(reb)
    mat_dt = matrix.index.get_level_values("datetime")
    matrix_reb = matrix[mat_dt.isin(reb_set)]
    fwd_reb = fwd.reindex(matrix_reb.index)

    n_reb = len(reb)
    n_train = int(floor(train_frac * n_reb))
    train_dates = set(reb[:n_train])
    test_dates = set(reb[n_train:])
    n_train_dates = len(train_dates)
    n_test_dates = len(test_dates)

    reb_dt = matrix_reb.index.get_level_values("datetime")
    train_mask = pd.Series(reb_dt.isin(train_dates), index=matrix_reb.index)
    test_mask = pd.Series(reb_dt.isin(test_dates), index=matrix_reb.index)

    if n_train_dates == 0:
        warnings.append("训练段无调仓日 (窗口太短或 train_frac 过小), 综合分可能全 NaN。")
    if n_test_dates == 0:
        warnings.append("测试段无调仓日 (窗口太短或 train_frac 过大), OOS 评测为空。")

    # 6) 合成 (拟合在 train, 综合分仅 test 有值)。失败 → fit_error。
    try:
        composite_series, weights = combine(matrix_reb, fwd_reb, method, train_mask, test_mask)
    except Exception as e:
        return ComposeResult(
            method=method,
            members=names,
            weights={},
            train_frac=train_frac,
            n_train_dates=n_train_dates,
            n_test_dates=n_test_dates,
            warnings=warnings,
            status="fit_error",
            error=f"{type(e).__name__}: {e}",
        )

    # 6.5) SP-3 SHAP: 仅 method='lgbm' 时, 从 combine 挂在 composite_series.attrs
    #      上的训好模型 + 测试集最后一日横截面 (一只股一行) 算 top-5 因子贡献.
    #      失败 (shap 缺失 / attrs 丢失 / 测试段空) 不影响主流程, 静默置 None.
    composite_shap_top5: Optional[Dict[str, List[Tuple[str, float]]]] = None
    if method == "lgbm":
        try:
            from financial_analyst.factors.eval.shap_explain import shap_top_k

            lgb_model = composite_series.attrs.get("_lgbm_model")
            if lgb_model is not None and n_test_dates > 0:
                # 测试段最后一日横截面 — 解释当下时点 (最新调仓日) 每只股的因子贡献.
                test_reb_dt = matrix_reb.index.get_level_values("datetime")[test_mask.to_numpy()]
                if len(test_reb_dt) > 0:
                    last_test_date = test_reb_dt.max()
                    test_matrix_last = matrix_reb.loc[
                        matrix_reb.index.get_level_values("datetime") == last_test_date,
                        list(names),
                    ].fillna(0.0)
                    # index 切到只剩 code (datetime 层只剩一日, 直接 droplevel).
                    test_matrix_last = test_matrix_last.copy()
                    test_matrix_last.index = test_matrix_last.index.get_level_values("code").astype(str)
                    # SHAP 按位置取贡献, 列名要复原成训练时的 f0/f1/... 保持与 model
                    # feature_name 一致再调用; shap_top_k 内部用 columns 名做映射,
                    # 这里直接传原 names (展示给用户的是因子表达式) — model 内部按
                    # 位置匹配, shap_top_k 返回时把这个 names 列名带出去, 用户友好.
                    composite_shap_top5 = shap_top_k(lgb_model, test_matrix_last, k=5)
        except Exception as e:
            warnings.append(f"SHAP 计算失败 (不影响主流程): {type(e).__name__}: {e}")
            composite_shap_top5 = None

    # 7) 综合分 OOS 评测。把综合分 reindex 到整面板 index (缺失 → NaN), 这样
    #    build_report 计算自己的前瞻收益并限制到调仓日后 dropna, 只在我们写过值的
    #    测试调仓行有值 → 自然只评 OOS。
    composite_full = composite_series.reindex(panel.df.index)
    composite_report = build_report(
        panel,
        lambda p: composite_full,
        config,
        f"composite[{method}]",
        "composite",
    )

    # 8) 成员同窗 OOS 对比: 每个成员构造"仅测试调仓行"的单因子 Series → build_report。
    member_oos: list = []
    for nm in names:
        try:
            compute = _resolve_member_compute(nm)
            raw = compute(panel)
        except Exception:
            member_oos.append(MemberOOS(name=nm, rank_ic=float("nan"), sharpe=float("nan")))
            continue
        if not isinstance(raw, pd.Series):
            member_oos.append(MemberOOS(name=nm, rank_ic=float("nan"), sharpe=float("nan")))
            continue
        # 仅保留测试调仓行的值, 其余 NaN → build_report dropna 后只评 OOS 同窗。
        member_full = pd.Series(float("nan"), index=panel.df.index, dtype="float64")
        member_test = raw.reindex(matrix_reb.index)[test_mask.to_numpy()]
        member_full.loc[member_test.index] = member_test.to_numpy()
        rep = build_report(panel, lambda p, _mf=member_full: _mf, config, nm, "member")
        rank_ic = _safe_float(rep.ic.rank_ic_mean) if rep.ic is not None else float("nan")
        sharpe = _safe_float(rep.portfolio.sharpe) if rep.portfolio is not None else float("nan")
        member_oos.append(MemberOOS(name=nm, rank_ic=rank_ic, sharpe=sharpe))

    # 9) verdict: 综合分 OOS Sharpe vs 最佳单成员 Sharpe。
    comp_sharpe = (
        _safe_float(composite_report.portfolio.sharpe)
        if (composite_report is not None and composite_report.portfolio is not None)
        else float("nan")
    )
    member_sharpes = [m.sharpe for m in member_oos if m.sharpe == m.sharpe]  # drop NaN
    best_member_sharpe = max(member_sharpes) if member_sharpes else float("nan")

    if comp_sharpe == comp_sharpe and best_member_sharpe == best_member_sharpe:
        delta = comp_sharpe - best_member_sharpe
        tail = f"增益 (+{delta:.2f})" if delta > 0 else "无明显增益"
        verdict = (
            f"综合分 OOS Sharpe {comp_sharpe:.2f} vs 最佳单成员 {best_member_sharpe:.2f} → {tail}"
        )
    else:
        verdict = (
            f"综合分 OOS Sharpe {comp_sharpe:.2f} vs 最佳单成员 "
            f"{best_member_sharpe:.2f} → 数据不足, 无法判定增益"
        )

    # 10) 低覆盖 / 短样本 warning。
    if composite_report is not None and composite_report.status == "ok":
        ch = composite_report.characteristics
        if ch is not None and ch.coverage == ch.coverage and ch.coverage < 0.5:
            warnings.append(f"综合分覆盖率低 ({ch.coverage:.0%}).")
    if n_test_dates < 12:
        warnings.append(f"OOS 测试调仓期太短 ({n_test_dates} < 12), 结论不稳健。")

    return ComposeResult(
        method=method,
        members=names,
        weights=weights,
        train_frac=train_frac,
        n_train_dates=n_train_dates,
        n_test_dates=n_test_dates,
        composite=composite_report,
        member_oos=member_oos,
        verdict=verdict,
        warnings=warnings,
        status="ok",
        error="",
        composite_shap_top5=composite_shap_top5,
    )
