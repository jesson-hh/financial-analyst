"""Workflow Lab v2 真节点 (SP-W2A).

5 个新节点替代 Phase 0 mock 三件套, 让用户能在 Workflow Lab 里搭真工作流:

| 节点 type | group | tag | params | output |
|---|---|---|---|---|
| ``data.universe`` | data | data | ``name`` (csi300/.../csi_fast/all) | ``list[str]`` 代码 |
| ``data.load_panel`` | data | data | ``freq``, ``start``, ``end`` | ``PanelData.df`` (DataFrame) |
| ``factor.from_registry`` | factor | factor | ``name`` (alpha 名 / user factor 名) | ``pd.Series`` alpha |
| ``factor.from_expression`` | factor | factor | ``expr`` (DSL) | ``pd.Series`` alpha |
| ``eval.factor_report`` | eval | factor,backtest | ``fwd_days``, ``n_groups``, ``cost_bps`` | ``dict`` (FactorReport asdict) |

import 本包即触发 ``@node`` 注册. 模块级 side-effect 加载 ``UserFactorStore``,
让 forge 因子 (family='user') 也能用 ``factor.from_registry(name=...)`` 调起.

跨节点数据流约定:
- ``data.load_panel`` 输出 ``PanelData.df`` (单层 DataFrame, artifact_store 落 parquet
  → 下游读回也是 DataFrame). ``factor.from_*`` 节点在 compute 入口把 DataFrame
  重新包成 ``PanelData(df)``.
- ``factor.from_*`` 输出 ``pd.Series`` (alpha 值, MultiIndex datetime+code). artifact_store
  写 Series → 读回是单列 DataFrame; ``eval.factor_report`` 节点入口取 ``df.iloc[:, 0]`` 取回.
- ``eval.factor_report`` 输出 ``dict`` (FactorReport asdict, NaN→null).
"""
from __future__ import annotations

# 子模块 import → @node 装饰器 side-effect 触发注册.
from financial_analyst.factors.workflow_nodes import data_nodes  # noqa: F401
from financial_analyst.factors.workflow_nodes import factor_nodes  # noqa: F401
from financial_analyst.factors.workflow_nodes import eval_nodes  # noqa: F401

# 加载 forge 因子到 alpha registry, 让 factor.from_registry 能用 user_xxx 名调起.
# SP-W2A: 这是 lazy-friendly side effect — 失败不阻断 import (e.g. ~/.financial-analyst
# 没初始化时 UserFactorStore().register_all() 静默返 0).
try:
    from financial_analyst.factors.forge import UserFactorStore
    UserFactorStore().register_all()
except Exception:
    pass

__all__ = ["data_nodes", "factor_nodes", "eval_nodes"]
