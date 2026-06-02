"""SP-3 SHAP top-k 因子贡献分解测试.

合成数据: 3 个真信号 + 7 个噪声因子, n=200 样本. 真信号控制 y 的主要变化,
所以对绝大多数样本 (≥80%) top-1 的 SHAP 贡献因子应落在 3 个真信号之一.
"""
from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from financial_analyst.factors.eval.shap_explain import shap_top_k


@pytest.fixture
def synthetic_model_and_matrix():
    """造 3 真信号 + 7 噪声, y 由真信号线性 + 小噪声合成. 返 (model, X_df, true_signal_names)."""
    rng = np.random.default_rng(42)
    n, p = 200, 10
    feat_names = [f"feat_{i}" for i in range(p)]
    X = rng.standard_normal((n, p))
    # 真信号 = 前 3 个 (强权重), 噪声 = 后 7 个 (无关).
    y = 2.0 * X[:, 0] + 1.8 * X[:, 1] - 1.5 * X[:, 2] + 0.1 * rng.standard_normal(n)

    # LGB 训练 (用 f0/f1/... 安全名, 跟 combine.py 一致, 也能用任意名 — 这里直接 feat_names).
    dtrain = lgb.Dataset(X, label=y, feature_name=feat_names)
    params = {
        "objective": "regression",
        "num_leaves": 15,
        "learning_rate": 0.05,
        "min_data_in_leaf": 10,
        "verbose": -1,
    }
    model = lgb.train(params, dtrain, num_boost_round=100)

    # feature matrix: index = code (字符串), columns = feat_names.
    codes = [f"SH60{i:04d}" for i in range(n)]
    X_df = pd.DataFrame(X, index=codes, columns=feat_names)
    true_signal_names = {"feat_0", "feat_1", "feat_2"}

    return model, X_df, true_signal_names


def test_shap_top_k_returns_dict_per_code(synthetic_model_and_matrix):
    """基本契约: 返 dict[code, list], 每只股票一项, list 长度 ≤ k."""
    model, X_df, _ = synthetic_model_and_matrix
    out = shap_top_k(model, X_df, k=3)

    assert isinstance(out, dict)
    assert len(out) == len(X_df)
    # 所有 code 都在 X_df.index.
    assert set(out.keys()) == set(X_df.index.astype(str))
    for code, contribs in out.items():
        assert isinstance(contribs, list)
        assert len(contribs) <= 3
        # 每项是 (str, float) tuple.
        for item in contribs:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], str)
            assert isinstance(item[1], float)


def test_shap_top_k_sorted_by_abs_contribution(synthetic_model_and_matrix):
    """list 按 |contrib| 降序排."""
    model, X_df, _ = synthetic_model_and_matrix
    out = shap_top_k(model, X_df, k=5)
    for code, contribs in out.items():
        abs_vals = [abs(v) for _, v in contribs]
        assert abs_vals == sorted(abs_vals, reverse=True), \
            f"code={code} list 未按 |contrib| 降序: {contribs}"


def test_shap_top_k_recovers_true_signals(synthetic_model_and_matrix):
    """≥80% 的样本 top-1 应命中真信号 (feat_0/feat_1/feat_2)."""
    model, X_df, true_signal_names = synthetic_model_and_matrix
    out = shap_top_k(model, X_df, k=3)

    hit_count = 0
    for code, contribs in out.items():
        if not contribs:
            continue
        top1_name = contribs[0][0]
        if top1_name in true_signal_names:
            hit_count += 1
    hit_rate = hit_count / len(out)
    assert hit_rate >= 0.8, f"top-1 命中真信号率 {hit_rate:.2%} < 80% (期望真信号主导)"


def test_shap_top_k_preserves_sign(synthetic_model_and_matrix):
    """contrib 保号: 既有正也有负 (真信号有 +2/+1.8/-1.5 不同方向)."""
    model, X_df, _ = synthetic_model_and_matrix
    out = shap_top_k(model, X_df, k=10)  # 拿全部 10 个
    signs = set()
    for contribs in out.values():
        for _, v in contribs:
            if v > 0:
                signs.add("+")
            elif v < 0:
                signs.add("-")
    assert signs == {"+", "-"}, f"应同时存在正负 SHAP, 实际只有 {signs}"


def test_shap_top_k_empty_matrix_returns_empty_dict(synthetic_model_and_matrix):
    """空矩阵 → 空 dict, 不抛."""
    model, X_df, _ = synthetic_model_and_matrix
    empty_df = X_df.iloc[:0]
    out = shap_top_k(model, empty_df, k=5)
    assert out == {}


def test_shap_top_k_k_larger_than_features(synthetic_model_and_matrix):
    """k > 特征数 → 每股最多返特征数个 (不报错, 不补零)."""
    model, X_df, _ = synthetic_model_and_matrix
    out = shap_top_k(model, X_df, k=999)
    for code, contribs in out.items():
        assert len(contribs) == X_df.shape[1]
