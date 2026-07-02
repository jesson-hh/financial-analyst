# tests/test_jump_model.py
# jump-penalty DP 门禁:全局最优(暴力对照)/分段还原/λ 压切换/确定性。
import numpy as np
import pytest
from guanlan_v2.strategy.compute.jump_model import (dp_states, fit_jump_model,
                                                    online_state, soft_prob, _objective)


def test_dp_optimal_vs_bruteforce():
    # T=8 全枚举 256 条路径,DP 结果目标值必须等于全局最优。
    rng = np.random.default_rng(0)
    X = rng.normal(size=(8, 2))
    C = np.array([[0.5, 0.0], [-0.5, 0.0]])
    lam = 0.3
    s = dp_states(X, C, lam)
    best = min(_objective(X, C, np.array([(b >> i) & 1 for i in range(8)]), lam)
               for b in range(2 ** 8))
    assert _objective(X, C, s, lam) == pytest.approx(best)


def test_fit_recovers_segmentation():
    # 两段清晰分离数据:恰 1 次切换,两段内各 ≥95% 同态(防塌缩单态)。
    rng = np.random.default_rng(1)
    X = np.vstack([rng.normal(1.0, 0.3, (100, 2)), rng.normal(-1.0, 0.3, (100, 2))])
    C, s, obj = fit_jump_model(X, k=2, lam=5.0, seed=0)
    assert int((s[1:] != s[:-1]).sum()) == 1
    assert (s[:100] == s[0]).mean() >= 0.95 and (s[100:] == s[-1]).mean() >= 0.95


def test_lambda_suppresses_switching():
    # 纯噪声:λ→大 切换次数被压死;λ=0 切换远多(证据:jump penalty 抑 whipsaw)。
    rng = np.random.default_rng(2)
    X = rng.normal(size=(300, 2))
    _, s0, _ = fit_jump_model(X, k=2, lam=0.0, seed=0)
    _, s9, _ = fit_jump_model(X, k=2, lam=1e6, seed=0)
    assert int((s9[1:] != s9[:-1]).sum()) <= 1 < int((s0[1:] != s0[:-1]).sum())


def test_fit_deterministic_and_online_consistent():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(120, 3))
    a = fit_jump_model(X, lam=10.0, seed=7)
    b = fit_jump_model(X, lam=10.0, seed=7)
    assert np.array_equal(a[1], b[1]) and np.allclose(a[0], b[0])
    # 在线过滤与软概率:argmax(soft) == online_state(同一代价函数)
    st = online_state(X[-1], a[0], 10.0, prev_state=int(a[1][-2]))
    p = soft_prob(X[-1], a[0], 10.0, prev_state=int(a[1][-2]), temp=1.0)
    assert int(np.argmax(p)) == st and p.sum() == pytest.approx(1.0)
