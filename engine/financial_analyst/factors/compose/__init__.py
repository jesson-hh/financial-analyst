"""SP-D 多因子合成 (Factor Composite Model).

导出因子矩阵构建器 + 编排入口 (compose_factors) + 结构化结果类型。
"""
from __future__ import annotations

from financial_analyst.factors.compose.compose import (
    ComposeResult,
    MemberOOS,
    compose_factors,
)
from financial_analyst.factors.compose.matrix import build_factor_matrix

__all__ = [
    "build_factor_matrix",
    "compose_factors",
    "ComposeResult",
    "MemberOOS",
]
