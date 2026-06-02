"""评测节点 — 单因子 IC / 分位 / 多空 / 特征报告.

SP-W2A spec §SP-W2A 表第 5 行.

一个节点:
- ``eval.factor_report`` (params.fwd_days/n_groups/cost_bps, inputs.alpha+panel)
  → ``dict`` (FactorReport asdict, NaN→null sanitized)
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from financial_analyst.workflow.registry import node


def _wrap_panel(upstream_panel: Any):
    """同 factor_nodes._wrap_panel — DataFrame → PanelData."""
    from financial_analyst.factors.zoo.panel import PanelData

    if isinstance(upstream_panel, PanelData):
        return upstream_panel
    if isinstance(upstream_panel, pd.DataFrame):
        return PanelData(upstream_panel)
    raise TypeError(
        f"eval 节点期望 inputs.panel 是 PanelData / DataFrame, 实际是 "
        f"{type(upstream_panel).__name__}"
    )


def _coerce_alpha(upstream_alpha: Any) -> pd.Series:
    """artifact_store 把 Series 落 parquet → 读回是单列 DataFrame. 这里取回 Series.

    若是 Series 直接透传 (单元测试可能直接给 Series).
    """
    if isinstance(upstream_alpha, pd.Series):
        return upstream_alpha
    if isinstance(upstream_alpha, pd.DataFrame):
        if upstream_alpha.shape[1] == 0:
            raise ValueError("eval.factor_report inputs.alpha DataFrame 没有列")
        return upstream_alpha.iloc[:, 0]
    raise TypeError(
        f"eval.factor_report 期望 inputs.alpha 是 Series / 单列 DataFrame, 实际是 "
        f"{type(upstream_alpha).__name__}"
    )


def _sanitize_nan(obj: Any) -> Any:
    """递归把 NaN / Inf 换成 None (跟 _jsonable 同语义), 让前端 JSON.parse 不挂.

    artifact_store 写 JSON 时会 ``allow_nan=False``, 没 sanitize 直接抛. 这里在
    eval 节点出口先 sanitize, 让 dict 输出能落盘.
    """
    import math

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan(v) for v in obj]
    return obj


class FactorReportParams(BaseModel):
    """factor_report 参数 (其余默认值跟 EvalConfig 走)."""

    fwd_days: int = Field(5, ge=1, description="前向收益窗口 (默认 5 个交易日).")
    n_groups: int = Field(10, ge=2, description="分位组数 (默认 10 分位).")
    cost_bps: float = Field(0.0, ge=0.0, description="单边交易成本 bps (默认 0).")
    freq: str = Field("day", description="调仓频率: day / week / month")


@node(
    type="eval.factor_report",
    params_model=FactorReportParams,
    risk="normal",
    pit=False,
    group="eval",
    tag=["factor", "backtest"],
    description="单因子标准评测 (RankIC / 分位组合 / 多空组合 / 因子特征), 返 FactorReport dict.",
)
def eval_factor_report(params: dict, inputs: dict) -> dict:
    """``build_report(panel, lambda p: alpha, EvalConfig(...), label, family) -> FactorReport``.

    用 lambda 包 alpha — alpha 已经算好了, build_report 内部 ``compute(panel)``
    其实就是直接拿到这个 alpha, 不会重算.
    """
    from financial_analyst.factors.eval import EvalConfig, build_report

    panel = _wrap_panel(inputs.get("panel"))
    alpha = _coerce_alpha(inputs.get("alpha"))

    # 用 panel 的 datetime 范围作为 config.start/end, freq 沿用 params.
    dates = panel.dates()
    if len(dates) == 0:
        raise ValueError("eval.factor_report panel 没有 datetime 数据")
    start = str(pd.Timestamp(dates.min()).date())
    end = str(pd.Timestamp(dates.max()).date())

    config = EvalConfig(
        universe="workflow",  # workflow 节点不知道原始 universe 名, 占位即可
        freq=params.get("freq", "day"),
        start=start,
        end=end,
        fwd_days=params.get("fwd_days", 5),
        n_groups=params.get("n_groups", 10),
        cost_bps=params.get("cost_bps", 0.0),
    )

    label = str(alpha.name) if alpha.name is not None else "workflow_alpha"
    rpt = build_report(panel, lambda p: alpha, config, label, family="workflow")
    return _sanitize_nan(asdict(rpt))


__all__ = ["FactorReportParams", "eval_factor_report"]
