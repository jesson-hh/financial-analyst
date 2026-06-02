"""Mock 节点 — 只供 Phase 0 端到端测试用, 不接任何真实数据源.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §8.

三个节点构成最小 DAG: ``data.constant_universe -> factor.zeros -> eval.row_count``,
让 runner + artifact_store + run_log 三层在一次 ``run()`` 里全部走通.

import 本模块即触发 ``@node`` 注册到全局 ``NodeRegistry``. 重复 import 时由
``NodeRegistry.register`` 抛 ``ValueError`` 阻挡 — Python 模块缓存让首次 import
后再次 import 不会重跑装饰器, 所以一进程一次注册是干净的.

Realign 阶段对齐 spec §8 (修 H6):

- ``data.constant_universe`` 输出**改为 dict** ``{"codes": [...], "n": len(codes)}``,
  挂 ``UniverseOutput`` 校验. 之前返 ``list[str]`` 不挂模型, 跟 §8.1 不一致.
- ``factor.zeros`` 输出**保持 DataFrame** (从 ``inputs["universe"]["codes"]`` 取列表,
  不再 ``inputs["universe"]`` 直接当 list — 因为上游现在返 dict). DataFrame 输出不挂
  ``outputs_model`` (runner ``_validate_outputs`` 对非 dict 跳过).
- ``eval.row_count`` 输入 key 改为 ``"frame"`` (spec §8.3), 输出改为 ``{"rows":N, "cols":M}``,
  挂 ``RowCountOutput``. 之前是 ``{"n": ...}`` + 输入 key ``"series"``, 跟 spec 错位.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from financial_analyst.workflow.registry import node


# ---------------------------------------------------------------------------
# 1. data.constant_universe — 返回固定股票池 dict ({"codes": [...], "n": int})
# ---------------------------------------------------------------------------


class UniverseParams(BaseModel):
    """常量股票池参数."""

    codes: list[str] = Field(default_factory=lambda: ["SH600519", "SZ000858"])


class UniverseOutput(BaseModel):
    """常量股票池输出形状 (spec §8.1)."""

    codes: list[str]
    n: int = Field(..., ge=0)


@node(
    type="data.constant_universe",
    params_model=UniverseParams,
    outputs_model=UniverseOutput,
    risk="normal",
    pit=False,
)
def constant_universe(params: dict, inputs: dict) -> dict:
    """返回 ``{"codes": params['codes'], "n": len(params['codes'])}``.

    runner 给 compute 的 params 是 ``params_model.model_validate(...).model_dump()``
    后的 dict, 所以这里取 ``params["codes"]``.
    """
    codes = list(params["codes"])
    return {"codes": codes, "n": len(codes)}


# ---------------------------------------------------------------------------
# 2. factor.zeros — 给上游 universe dict, 返回 DataFrame[code, value=0.0]
# ---------------------------------------------------------------------------


class ZerosParams(BaseModel):
    """空 params (zeros 不需要外部参数)."""

    pass


@node(
    type="factor.zeros",
    params_model=ZerosParams,
    risk="normal",
)
def factor_zeros(params: dict, inputs: dict) -> pd.DataFrame:
    """构造 ``DataFrame({"code": codes, "value": [0.0] * n})``.

    输入: ``inputs["universe"]`` = 上游 ``constant_universe`` 的输出 dict
    ``{"codes": [...], "n": int}`` (artifact_store 反序列化回 dict).

    输出 DataFrame 不挂 ``outputs_model`` — 这是 runner H3 修法的契约:
    outputs_model 只针对 dict 输出, DataFrame 输出豁免 (单元测试已覆盖).
    """
    upstream = inputs["universe"]
    codes = list(upstream["codes"])
    return pd.DataFrame({"code": codes, "value": [0.0] * len(codes)})


# ---------------------------------------------------------------------------
# 3. eval.row_count — 输入 DataFrame, 返回 {"rows": N, "cols": M}
# ---------------------------------------------------------------------------


class RowCountOutput(BaseModel):
    """形状校验: ``rows`` / ``cols`` 必须非负整数 (spec §8.3)."""

    rows: int = Field(..., ge=0)
    cols: int = Field(..., ge=0)


@node(
    type="eval.row_count",
    outputs_model=RowCountOutput,
    risk="normal",
)
def row_count(params: dict, inputs: dict) -> dict:
    """返回 ``{"rows": len(df), "cols": df.shape[1]}``.

    上游 ``factor.zeros`` 输出 ``DataFrame``, artifact_store 写 parquet, 读回仍是
    ``DataFrame``. ``inputs["frame"]`` 直接拿到 DataFrame, ``len(df)`` = 行数,
    ``df.shape[1]`` = 列数 (含 ``code`` 与 ``value`` 两列).
    """
    df = inputs["frame"]
    return {"rows": int(len(df)), "cols": int(df.shape[1])}


__all__ = [
    "UniverseParams",
    "UniverseOutput",
    "ZerosParams",
    "RowCountOutput",
    "constant_universe",
    "factor_zeros",
    "row_count",
]
