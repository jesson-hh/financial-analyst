"""因子节点 — 注册名查找 / DSL 表达式编译.

SP-W2A spec §SP-W2A 表第 2/3 行.

两个节点都吃上游 panel (DataFrame from data.load_panel) → 返 pd.Series alpha:
- ``factor.from_registry`` (params.name) — 442 alpha + user_xxx forge 因子
- ``factor.from_expression`` (params.expr) — DSL 白名单表达式
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from financial_analyst.workflow.registry import node


def _wrap_panel(upstream_panel: Any):
    """artifact_store 写 panel.df (DataFrame) → 读回是 DataFrame. 这里包成 PanelData.

    若已是 PanelData (单元测试可能直接传) — 透传.
    """
    from financial_analyst.factors.zoo.panel import PanelData

    if isinstance(upstream_panel, PanelData):
        return upstream_panel
    if isinstance(upstream_panel, pd.DataFrame):
        return PanelData(upstream_panel)
    raise TypeError(
        f"factor 节点期望 inputs.panel 是 PanelData / DataFrame, 实际是 "
        f"{type(upstream_panel).__name__}"
    )


# ---------------------------------------------------------------------------
# 1. factor.from_registry — params.name → AlphaSpec.compute(panel) → Series
# ---------------------------------------------------------------------------


class FromRegistryParams(BaseModel):
    """注册因子名."""

    name: str = Field(..., description="因子名: alpha001 / rev_20 / user_xxx 等")


@node(
    type="factor.from_registry",
    params_model=FromRegistryParams,
    risk="normal",
    pit=False,
    group="factor",
    tag=["factor"],
    description="按名取注册因子 (442 内置 alpha + user 炼因子), 在 panel 上计算返 pd.Series.",
)
def factor_from_registry(params: dict, inputs: dict) -> pd.Series:
    """``registry.get(name).compute(panel) -> pd.Series``.

    442 内置 alpha 在 ``financial_analyst.factors.zoo`` import 时自动注册, user 炼
    因子由 ``factors.workflow_nodes.__init__`` 调 ``UserFactorStore().register_all()``
    注册. 调本节点前两批都已 ready.
    """
    # 保险触发 zoo families import (在 lazy 模式下 __init__ 不会自动 register)
    import financial_analyst.factors.zoo  # noqa: F401

    from financial_analyst.factors.zoo.registry import get as get_alpha

    name = params["name"]
    panel = _wrap_panel(inputs.get("panel"))
    spec = get_alpha(name)  # KeyError if missing
    series = spec.compute(panel)
    if not isinstance(series, pd.Series):
        raise TypeError(
            f"factor {name!r} compute 返回 {type(series).__name__}, 期望 pd.Series"
        )
    # 给 series.name 一个稳定值, parquet 反序列化时方便回认.
    if series.name is None or str(series.name).strip() == "":
        series = series.rename(name)
    return series


# ---------------------------------------------------------------------------
# 2. factor.from_expression — params.expr → compile_factor(expr)(panel) → Series
# ---------------------------------------------------------------------------


class FromExpressionParams(BaseModel):
    """DSL 因子表达式 (受限 namespace)."""

    expr: str = Field(..., description="白名单 DSL 表达式, e.g. rank(-delta(close,5))")


@node(
    type="factor.from_expression",
    params_model=FromExpressionParams,
    risk="normal",
    pit=False,
    group="factor",
    tag=["factor"],
    description="编译白名单 DSL 表达式 (rank/ts_rank/delta 等算子 + close/volume 等字段), 返 pd.Series.",
)
def factor_from_expression(params: dict, inputs: dict) -> pd.Series:
    """``compile_factor(expr)(panel) -> pd.Series``. 先 ``validate_expr`` 拒 __ / import / lambda."""
    from financial_analyst.factors.zoo.expr import compile_factor, validate_expr

    expr = params["expr"]
    validate_expr(expr)
    compute = compile_factor(expr)
    panel = _wrap_panel(inputs.get("panel"))
    series = compute(panel)
    if not isinstance(series, pd.Series):
        raise TypeError(
            f"expression {expr!r} 返回 {type(series).__name__}, 期望 pd.Series"
        )
    if series.name is None or str(series.name).strip() == "":
        series = series.rename("expr")
    return series


__all__ = [
    "FromRegistryParams",
    "FromExpressionParams",
    "factor_from_registry",
    "factor_from_expression",
]
