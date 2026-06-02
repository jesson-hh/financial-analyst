"""SP-3 SHAP 可解释性: 单股票 top-k 因子贡献分解.

给一个训好的 LightGBM Booster + 该模型的特征矩阵 (index=code), 返回
``{code: [(factor, signed_contrib), ...]}`` —— 每只股票按 SHAP 贡献绝对值
降序取前 k 个, 保留符号 (正=推涨 / 负=推跌, 调用方按 abs 或 signed 决定展示
顺序).

实现走 ``shap.TreeExplainer`` (官方 LightGBM 加速通路, 无需采样, exact 树
路径分解), 一次性算整矩阵 (n, p) 的 SHAP, 然后每行向量按 |contrib| 排序
取 top-k. ``shap`` 是可选依赖, 缺失时 import 由调用方处理 (本模块顶层导入,
调用方按需 catch ImportError).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap


def shap_top_k(
    model: lgb.Booster,
    feature_matrix: pd.DataFrame,
    k: int = 5,
) -> Dict[str, List[Tuple[str, float]]]:
    """每只股票算 SHAP, 返按 |contrib| 降序的 top-k 因子贡献.

    Parameters
    ----------
    model : lgb.Booster
        已训好的 LightGBM 模型 (回归/分类皆可; 回归时 SHAP 直接对应预测值贡献).
    feature_matrix : pd.DataFrame
        ``index = code`` (str), ``columns = factor names`` (与训练特征顺序一致).
        允许 NaN, ``shap`` 会按 LightGBM 的 missing 处理路径走.
    k : int
        每只股票保留的 top-k 贡献数 (按 |contrib| 排), 默认 5.

    Returns
    -------
    dict[str, list[tuple[str, float]]]
        ``{code: [(factor_name, signed_contrib_float), ...]}``. 列表已按
        ``|contrib|`` 降序排, 长度 ``min(k, n_features)``. 符号保留 ——
        正值=该因子在这一刻推涨预测, 负值=推跌. 空矩阵 → 空 dict.

    Notes
    -----
    - ``shap.TreeExplainer(model).shap_values(X)`` 对 LightGBM 走原生
      TreeSHAP 路径, 复杂度 O(n * L * D^2) (L=叶子, D=深度), 万级特征 *
      千级股票仍亚秒级.
    - 多分类时 ``shap_values`` 会返 list[ndarray] (每类一份), 当前只支持
      单输出回归 / 二分类 (TreeExplainer 会自动归并到单 ndarray (n, p)).
    - feature_matrix 的列顺序必须与 ``model.feature_name()`` 顺序一致 ——
      调用方负责对齐 (compose.combine._combine_lgbm 用 f0/f1/... 安全名,
      复用时按位置就是). 这里不做名字校验, 只按列位置取贡献.
    """
    if feature_matrix.empty:
        return {}

    # TreeExplainer 对 LightGBM 走 exact 路径. ``shap_values`` 对回归返
    # (n, p) ndarray; 对二分类返 (n, p) 或 list (一份正/一份负, 取一即可).
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(feature_matrix)

    # 兼容旧版 list[ndarray] (多分类): 取首个 (二分类时正类). 单输出回归直接 ndarray.
    if isinstance(sv, list):
        sv = sv[0] if len(sv) > 0 else np.zeros_like(feature_matrix.to_numpy())
    sv = np.asarray(sv, dtype=float)

    # 形状校验: 行=股票数, 列=因子数.
    n, p = feature_matrix.shape
    if sv.shape != (n, p):
        # 极少见: 某些版本会多一列基线 (expected value). 截到前 p 列保平安.
        sv = sv[:, :p]

    codes = feature_matrix.index.astype(str).tolist()
    factor_names = list(feature_matrix.columns)
    k_eff = min(k, p)

    result: Dict[str, List[Tuple[str, float]]] = {}
    for i, code in enumerate(codes):
        row = sv[i, :]
        # argsort 升序 → 取末 k_eff 个再倒序 = |contrib| 降序.
        order = np.argsort(-np.abs(row))[:k_eff]
        result[code] = [(factor_names[j], float(row[j])) for j in order]

    return result
