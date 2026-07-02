# -*- coding: utf-8 -*-
"""jump-penalty 统计跳变模型(纯 numpy,零新依赖)。

目标:min Σ_t ‖x_t − μ_{s_t}‖² + λ·Σ_t 1[s_t≠s_{t−1}](Nystrup 型 statistical jump model)。
求解:质心固定 → 状态序列 DP 全局最优;状态固定 → 质心=均值;交替迭代,多初始化取最优。
证据(深研 3-0):jump penalty 抑制 whipsaw,年切换 ~0.8 次 vs 裸 HMM 2+。
"""
from __future__ import annotations

import numpy as np


def dp_states(X, centers, lam, prev_state=None):
    """给定质心求全局最优状态序列(动态规划)。X:(T,F) centers:(k,F) λ=切换罚。
    prev_state 非空 → 首日也按「从 prev_state 切换」计罚(在线续推口径)。"""
    X = np.asarray(X, dtype=np.float64)
    C = np.asarray(centers, dtype=np.float64)
    T, k = len(X), len(C)
    d2 = ((X[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)   # (T,k) 逐点代价
    cost = np.full((T, k), np.inf)
    back = np.zeros((T, k), dtype=np.int64)
    if prev_state is None:
        cost[0] = d2[0]
    else:
        cost[0] = d2[0] + lam * (np.arange(k) != int(prev_state))
    for t in range(1, T):
        trans = cost[t - 1][:, None] + lam * (1.0 - np.eye(k))   # trans[j,s]
        back[t] = np.argmin(trans, axis=0)
        cost[t] = d2[t] + np.min(trans, axis=0)
    s = np.zeros(T, dtype=np.int64)
    s[-1] = int(np.argmin(cost[-1]))
    for t in range(T - 2, -1, -1):
        s[t] = back[t + 1][s[t + 1]]
    return s


def _objective(X, C, s, lam):
    X = np.asarray(X, dtype=np.float64)
    C = np.asarray(C, dtype=np.float64)
    s = np.asarray(s, dtype=np.int64)
    return float(((X - C[s]) ** 2).sum() + lam * int((s[1:] != s[:-1]).sum()))


def fit_jump_model(X, k=2, lam=100.0, n_init=6, max_iter=25, seed=0, warm_centers=None):
    """交替优化 + 多随机初始化(+可选 warm start)。返回 (centers(k,F), states(T,), obj)。
    质心行序无语义,有利态由调用方按特征维命名(factor_regime 按 sortino20 维)。"""
    X = np.asarray(X, dtype=np.float64)
    T = len(X)
    rng = np.random.default_rng(seed)
    inits = [warm_centers] if warm_centers is not None else []
    for _ in range(n_init):
        inits.append(X[rng.choice(T, size=k, replace=False)].copy())
    best = None
    for C0 in inits:
        C = np.asarray(C0, dtype=np.float64).copy()
        s = dp_states(X, C, lam)
        for _ in range(max_iter):
            C_new = np.vstack([X[s == j].mean(axis=0) if (s == j).any() else C[j]
                               for j in range(k)])
            s_new = dp_states(X, C_new, lam)
            done = np.array_equal(s_new, s) and np.allclose(C_new, C)
            C, s = C_new, s_new
            if done:
                break
        obj = _objective(X, C, s, lam)
        if best is None or obj < best[2]:
            best = (C, s, obj)
    return best


def online_state(x, centers, lam, prev_state):
    """在线过滤(月度重拟之间逐日用,O(k)):cost_s = ‖x−μ_s‖² + λ·1[s≠prev] → argmin。"""
    x = np.asarray(x, dtype=np.float64)
    C = np.asarray(centers, dtype=np.float64)
    cost = ((C - x) ** 2).sum(axis=1) + lam * (np.arange(len(C)) != int(prev_state))
    return int(np.argmin(cost))


def soft_prob(x, centers, lam, prev_state, temp):
    """状态软概率 = softmax(−cost/temp);temp=拟合残差均值(调用方传),下限 1e-9 防除零。"""
    x = np.asarray(x, dtype=np.float64)
    C = np.asarray(centers, dtype=np.float64)
    cost = ((C - x) ** 2).sum(axis=1) + lam * (np.arange(len(C)) != int(prev_state))
    z = -cost / max(float(temp), 1e-9)
    z -= z.max()
    e = np.exp(z)
    return e / e.sum()
