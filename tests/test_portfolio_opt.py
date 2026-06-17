# tests/test_portfolio_opt.py
# 组合优化器(guanlan_v2/workflow/portfolio_opt.py)纯数学门禁,聚焦 (b) 预测波动注入:
#   rescale_cov_vols(保相关、换对角波动) + optimize_weights(target_vols=) 闭环。
# 不碰数据、不连服务器、不依赖 engine —— 纯 numpy/scipy。
import numpy as np
import pytest

from guanlan_v2.workflow.portfolio_opt import (
    rescale_cov_vols,
    optimize_weights,
    min_var_weights,
    shrink_cov,
)


def _corr(S):
    d = np.sqrt(np.diag(S))
    return S / np.outer(d, d)


# ── rescale_cov_vols:换对角波动、保相关结构 ───────────────────────────────────
def test_rescale_replaces_diagonal_preserves_correlation():
    S = np.array([[4.0, 1.0], [1.0, 9.0]])        # 波动 2,3;相关 1/6
    out = rescale_cov_vols(S, np.array([1.0, 3.0]))   # 资产0 波动 2→1
    assert out[0, 0] == pytest.approx(1.0)            # 对角 = 目标波动²
    assert out[1, 1] == pytest.approx(9.0)
    assert out[0, 1] == pytest.approx(0.5)            # 协方差 = 相关 1/6 × 1 × 3
    np.testing.assert_allclose(_corr(out), _corr(S), rtol=1e-9)  # 相关矩阵不变
    np.testing.assert_allclose(out, out.T, rtol=1e-12)           # 对称


def test_rescale_keeps_original_where_target_invalid():
    S = np.array([[4.0, 1.0], [1.0, 9.0]])
    out = rescale_cov_vols(S, np.array([np.nan, 3.0]))   # 资产0 目标非法 → 保原波动 2
    np.testing.assert_allclose(out, S, rtol=1e-9)        # 完全等于原矩阵
    out2 = rescale_cov_vols(S, np.array([0.0, 3.0]))     # 0 也算非法 → 保原
    np.testing.assert_allclose(out2, S, rtol=1e-9)


def test_rescale_zero_variance_row_safe():
    S = np.array([[0.0, 0.0], [0.0, 9.0]])               # 资产0 零方差(病态)
    out = rescale_cov_vols(S, np.array([2.0, 3.0]))
    assert np.all(np.isfinite(out))


# ── optimize_weights(target_vols=):预测波动改变定权 ──────────────────────────
def test_optimize_weights_target_vols_shifts_min_var():
    """同一相关结构下,把某资产的(预测)波动调高 → 最小方差给它更少权重。"""
    rng = np.random.default_rng(123)
    R = rng.standard_normal((300, 3)) * np.array([0.01, 0.01, 0.01])
    w_base, _ = optimize_weights(R, "min_var")
    # 资产2 预测波动翻 3 倍(其余不变)→ 应被压低权重
    Sig, _ = shrink_cov(R)
    base_vol = np.sqrt(np.diag(Sig))
    tv = base_vol.copy()
    tv[2] *= 3.0
    w_fore, note = optimize_weights(R, "min_var", target_vols=tv)
    assert w_fore is not None
    assert w_fore[2] < w_base[2] - 1e-4                  # 高预测波动 → 更低权重
    assert abs(float(w_fore.sum()) - 1.0) < 1e-6


def test_optimize_weights_target_vols_none_is_unchanged():
    rng = np.random.default_rng(7)
    R = rng.standard_normal((200, 4)) * 0.012
    w1, _ = optimize_weights(R, "min_var")
    w2, _ = optimize_weights(R, "min_var", target_vols=None)
    np.testing.assert_allclose(w1, w2, rtol=1e-9)        # target_vols=None ≡ 原行为


# ── risk_contributions:组合层风险归因(欧拉分解 MCR/CR/成分VaR)──────────────────
def test_risk_contributions_euler_identity():
    """成分风险贡献加总 = 组合波动(欧拉恒等);占比加总 = 1;CRᵢ = wᵢ·MCRᵢ。"""
    from guanlan_v2.workflow.portfolio_opt import risk_contributions
    S = np.array([[4.0, 1.0, 0.0], [1.0, 9.0, 2.0], [0.0, 2.0, 16.0]])
    w = np.array([0.5, 0.3, 0.2])
    rc = risk_contributions(S, w)
    port_vol = float(np.sqrt(w @ S @ w))
    assert rc["port_vol"] == pytest.approx(port_vol)
    assert sum(rc["cr"]) == pytest.approx(port_vol)            # Σ CRᵢ = σ_p
    assert sum(rc["pct"]) == pytest.approx(1.0)                # Σ pctᵢ = 1
    for i in range(3):
        assert rc["cr"][i] == pytest.approx(w[i] * rc["mcr"][i])


def test_risk_contributions_erc_equal():
    """ERC 权重下各资产成分风险贡献近似相等(占比≈1/N,离散度极小)。"""
    from guanlan_v2.workflow.portfolio_opt import risk_contributions, risk_parity_weights
    S = np.array([[4.0, 1.0, 0.0], [1.0, 9.0, 2.0], [0.0, 2.0, 16.0]])
    w = risk_parity_weights(S)
    rc = risk_contributions(S, w)
    assert np.std(rc["pct"]) < 0.01                           # 各占比≈1/3


def test_risk_contributions_component_var():
    """成分 VaR = z·CR(正态近似);加总 = 组合 VaR(z·σ_p)。"""
    from guanlan_v2.workflow.portfolio_opt import risk_contributions
    S = np.array([[4.0, 1.0], [1.0, 9.0]])
    w = np.array([0.6, 0.4])
    rc = risk_contributions(S, w)
    z95 = 1.6448536269514722
    for i in range(2):
        assert rc["comp_var95"][i] == pytest.approx(z95 * rc["cr"][i])
    assert sum(rc["comp_var95"]) == pytest.approx(z95 * rc["port_vol"])


def test_risk_contributions_concentrated():
    """单票满仓 → 该票风险占比≈1,其余≈0。"""
    from guanlan_v2.workflow.portfolio_opt import risk_contributions
    S = np.array([[4.0, 1.0], [1.0, 9.0]])
    rc = risk_contributions(S, np.array([1.0, 0.0]))
    assert rc["pct"][0] == pytest.approx(1.0)
    assert rc["pct"][1] == pytest.approx(0.0)


def test_risk_contributions_degenerate_honest():
    """零协方差(组合波动=0)→ 诚实降级不抛:port_vol=0、占比 None(0/0 无定义绝不编)。"""
    from guanlan_v2.workflow.portfolio_opt import risk_contributions
    rc = risk_contributions(np.zeros((2, 2)), np.array([0.5, 0.5]))
    assert rc["port_vol"] == 0.0
    assert rc["pct"] is None


# ── Black-Litterman:LLM 观点融合配置(均衡先验 + 观点后验)──────────────────────
def test_bl_no_views_recovers_market():
    """无观点 → BL 后验=均衡先验 Π,BL 权重≈市值权重 w_mkt(自洽铁律)。"""
    from guanlan_v2.workflow.portfolio_opt import black_litterman_posterior, black_litterman_weights
    S = np.array([[0.04, 0.01, 0.0], [0.01, 0.09, 0.02], [0.0, 0.02, 0.16]])
    wmkt = np.array([0.5, 0.3, 0.2])
    ER, Pi = black_litterman_posterior(S, wmkt, np.zeros((0, 3)), np.zeros(0), np.zeros((0, 0)),
                                       delta=2.5, tau=0.05)
    np.testing.assert_allclose(ER, Pi, rtol=1e-9)               # 无观点 → 后验=先验
    np.testing.assert_allclose(Pi, 2.5 * (S @ wmkt), rtol=1e-9)  # Π=δΣw_mkt
    w = black_litterman_weights(S, wmkt, np.zeros((0, 3)), np.zeros(0), np.zeros((0, 0)),
                                delta=2.5, tau=0.05)
    np.testing.assert_allclose(w, wmkt, atol=0.02)             # 权重≈市值权重(无观点不偏离均衡)


def test_bl_bullish_view_tilts_weight():
    """对资产0 的强看多绝对观点(高 Q、低 Ω)→ 资产0 后验收益↑、权重高于市值。"""
    from guanlan_v2.workflow.portfolio_opt import black_litterman_posterior, black_litterman_weights
    S = np.array([[0.04, 0.01, 0.0], [0.01, 0.09, 0.02], [0.0, 0.02, 0.16]])
    wmkt = np.array([0.4, 0.4, 0.2])
    _, Pi = black_litterman_posterior(S, wmkt, np.zeros((0, 3)), np.zeros(0), np.zeros((0, 0)))
    P = np.array([[1.0, 0.0, 0.0]]); Q = np.array([Pi[0] + 0.10]); Om = np.array([[1e-4]])
    ER, _ = black_litterman_posterior(S, wmkt, P, Q, Om)
    assert ER[0] > Pi[0] + 1e-4                                # 资产0 后验收益升
    w = black_litterman_weights(S, wmkt, P, Q, Om)
    assert w[0] > wmkt[0]                                      # 权重高于市值


def test_bl_confidence_monotonic():
    """观点置信度越高(Ω 越小)→ 后验越靠近观点 Q。"""
    from guanlan_v2.workflow.portfolio_opt import black_litterman_posterior
    S = np.array([[0.04, 0.01], [0.01, 0.09]]); wmkt = np.array([0.6, 0.4])
    _, Pi = black_litterman_posterior(S, wmkt, np.zeros((0, 2)), np.zeros(0), np.zeros((0, 0)))
    P = np.array([[1.0, 0.0]]); Q = np.array([Pi[0] + 0.20])
    er_lo, _ = black_litterman_posterior(S, wmkt, P, Q, np.array([[0.01]]))
    er_hi, _ = black_litterman_posterior(S, wmkt, P, Q, np.array([[1e-4]]))
    assert abs(er_hi[0] - Q[0]) < abs(er_lo[0] - Q[0])        # 高置信更靠观点


def test_bl_degenerate_honest():
    """奇异 Σ → 诚实降级不抛(返回 None 或合法长仓权重)。"""
    from guanlan_v2.workflow.portfolio_opt import black_litterman_weights
    w = black_litterman_weights(np.zeros((2, 2)), np.array([0.5, 0.5]),
                                np.zeros((0, 2)), np.zeros(0), np.zeros((0, 0)))
    assert w is None or (np.all(np.isfinite(w)) and abs(float(w.sum()) - 1.0) < 1e-6)
