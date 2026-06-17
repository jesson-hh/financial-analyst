"""SP-D 合成器: 4 个截面综合打分器 + 分发器 (combine).

每个合成器在 **训练行 (train_mask)** 上拟合, 综合分 Series **仅在测试行
(test_mask) 有值, 训练行恒为 NaN** —— 这是样本外 (OOS) 纪律的核心: 下游
build_report 的 dropna 会自然地只评测试段, 且权重计算绝不看测试行 (no leakage)。

入参:
    matrix:     因子矩阵 DataFrame, index=(datetime, code), columns=因子名。
    fwd:        前瞻收益 Series, 与 matrix 同 index。
    method:     equal / ic_weighted / linear / lgbm。
    train_mask: matrix.index 上的布尔 Series, True=训练行。
    test_mask:  matrix.index 上的布尔 Series, True=测试行。

返回 (composite_series, weights_dict)。weights_dict 键 == matrix 列。

异常策略: 拟合失败 (lstsq 奇异 / lgbm 抛错) 直接向上传播, 由 compose.py 捕获并
归为 fit_error。本模块不吞异常 (除非守卫: 训练行过少时仍尝试, 不主动崩)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _empty_composite(index: pd.Index) -> pd.Series:
    """全 NaN 的综合分骨架 (索引对齐 matrix)。"""
    return pd.Series(np.nan, index=index, dtype="float64", name="composite")


# ---------------------------------------------------------------------------
# equal: 等权行均值。
# ---------------------------------------------------------------------------
def _combine_equal(
    matrix: pd.DataFrame,
    fwd: pd.Series,
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[pd.Series, dict]:
    n = matrix.shape[1]
    weights = {col: 1.0 / n for col in matrix.columns}
    composite = _empty_composite(matrix.index)
    # 行均值 (跳过 NaN), 仅写入测试行。
    row_mean = matrix.mean(axis=1)
    composite.loc[test_mask] = row_mean.loc[test_mask]
    return composite, weights


# ---------------------------------------------------------------------------
# ic_weighted: 训练行上每列对 fwd 的整体 Spearman (rank-IC), 权重保号。
# ---------------------------------------------------------------------------
def _rank_ic(col: pd.Series, target: pd.Series) -> float:
    """整体 Spearman: 对两列做秩变换后求 Pearson 相关。

    在传入的 (已限制到训练行的) 样本上计算; 先 dropna 对齐两侧有效值。
    退化情形 (有效样本 < 2 或任一侧零方差) 返回 0.0。
    """
    df = pd.concat([col, target], axis=1, keys=["x", "y"]).dropna()
    if len(df) < 2:
        return 0.0
    rx = df["x"].rank()
    ry = df["y"].rank()
    if rx.std(ddof=0) == 0 or ry.std(ddof=0) == 0:
        return 0.0
    ic = rx.corr(ry)  # Pearson on ranks == Spearman
    if not np.isfinite(ic):
        return 0.0
    return float(ic)


def _combine_ic_weighted(
    matrix: pd.DataFrame,
    fwd: pd.Series,
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[pd.Series, dict]:
    train_matrix = matrix.loc[train_mask]
    train_fwd = fwd.loc[train_mask]

    weights = {col: _rank_ic(train_matrix[col], train_fwd) for col in matrix.columns}
    w = pd.Series(weights)

    composite = _empty_composite(matrix.index)
    # 综合分 (测试行) = Σ w_i · factor_i。NaN 因子项不计入 (fillna 0)。
    test_matrix = matrix.loc[test_mask]
    scored = test_matrix.fillna(0.0).mul(w, axis=1).sum(axis=1)
    composite.loc[test_mask] = scored
    return composite, weights


# ---------------------------------------------------------------------------
# linear: 训练行 pooled OLS fwd ~ factors + intercept, via numpy.linalg.lstsq。
# ---------------------------------------------------------------------------
def _combine_linear(
    matrix: pd.DataFrame,
    fwd: pd.Series,
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[pd.Series, dict]:
    cols = list(matrix.columns)

    # 训练设计矩阵: 先 drop 含 NaN 的行 (因子或 fwd 任一 NaN)。
    train_df = matrix.loc[train_mask].copy()
    train_df["__fwd__"] = fwd.loc[train_mask]
    train_df = train_df.dropna()

    X_train = train_df[cols].to_numpy(dtype="float64")
    y_train = train_df["__fwd__"].to_numpy(dtype="float64")
    # 加截距列。
    ones = np.ones((X_train.shape[0], 1), dtype="float64")
    A = np.hstack([X_train, ones])

    # 奇异/秩亏时 lstsq 走最小范数解 (不抛); 真正异常 (如空矩阵) 向上传播。
    coef, *_ = np.linalg.lstsq(A, y_train, rcond=None)
    factor_coef = coef[:-1]  # 末列为截距, 排除出 weights
    intercept = coef[-1]
    weights = {col: float(c) for col, c in zip(cols, factor_coef)}

    composite = _empty_composite(matrix.index)
    test_matrix = matrix.loc[test_mask, cols]
    X_test = test_matrix.fillna(0.0).to_numpy(dtype="float64")
    pred = X_test @ factor_coef + intercept  # 截距不影响截面排序, 保留无碍
    composite.loc[test_mask] = pred
    return composite, weights


# ---------------------------------------------------------------------------
# lgbm: 训练行 pooled LightGBM 回归 (factors -> fwd)。
# ---------------------------------------------------------------------------
def _combine_lgbm(
    matrix: pd.DataFrame,
    fwd: pd.Series,
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[pd.Series, dict]:
    import lightgbm as lgb  # 核心依赖

    cols = list(matrix.columns)

    train_df = matrix.loc[train_mask].copy()
    train_df["__fwd__"] = fwd.loc[train_mask]
    train_df = train_df.dropna()

    X_train = train_df[cols].to_numpy(dtype="float64")
    y_train = train_df["__fwd__"].to_numpy(dtype="float64")

    # LightGBM 拒绝含 JSON 特殊字符的 feature_name (成员可为表达式如
    # "rank(-delta(close,5))", 含 ( ) , -)。用位置化安全名 f0/f1/...; weights 仍
    # 按位置映射回原列名 cols, 故对外不可见。
    feat_names = [f"f{i}" for i in range(len(cols))]

    params = {
        "objective": "regression",
        "num_leaves": 15,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "min_data_in_leaf": 20,
        "verbose": -1,
    }
    dtrain = lgb.Dataset(X_train, label=y_train, feature_name=feat_names)
    model = lgb.train(params, dtrain, num_boost_round=100)

    # 特征重要度 (gain) 归一化; 全零时保持全零。
    imp = np.asarray(model.feature_importance(importance_type="gain"), dtype="float64")
    total = imp.sum()
    if total > 0:
        imp = imp / total
    weights = {col: float(v) for col, v in zip(cols, imp)}

    composite = _empty_composite(matrix.index)
    test_matrix = matrix.loc[test_mask, cols]
    X_test = test_matrix.fillna(0.0).to_numpy(dtype="float64")
    if X_test.shape[0] > 0:
        composite.loc[test_mask] = model.predict(X_test)
    return composite, weights


# ---------------------------------------------------------------------------
# 分发器。
# ---------------------------------------------------------------------------
_DISPATCH = {
    "equal": _combine_equal,
    "ic_weighted": _combine_ic_weighted,
    "linear": _combine_linear,
    "lgbm": _combine_lgbm,
}


def combine(
    matrix: pd.DataFrame,
    fwd: pd.Series,
    method: str,
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[pd.Series, dict]:
    """按 method 分发到对应合成器。未知 method -> ValueError。

    综合分 Series 仅在 test_mask 行有值 (train 行 NaN)。weights dict 键 == matrix 列。
    """
    fn = _DISPATCH.get(method)
    if fn is None:
        raise ValueError(
            f"未知合成方法 {method!r}; 支持: {sorted(_DISPATCH)}"
        )
    return fn(matrix, fwd, train_mask, test_mask)
