# -*- coding: utf-8 -*-
"""#7 FinCast/FM 在线化 — v4 B3 集成读取侧(纯函数)测试。

契约(``guanlan_v2.strategy.compute.v4_fincast``):
  · ``b3_mix_scores(score_lgb, score_fincast, w_fc=None)`` —— LGB + FinCast z-score 加权混合
    (B3 集成,镜像 dormant qlib ``vendor/v4_ranking.py:195-258``)。
      - FinCast 匹配 <50 只 → 诚实退化纯 LGB(score 原样返回,active=False);
      - 默认 w_fc=0.4(w_lgb=0.6);传入则按 [0.1,0.5] 夹;
      - z-score 退化(std=0)→ 该腿全 0 不崩。
  · ``apply_fincast_ensemble(pred, ld, fincast_path)`` —— 只读 parquet → 写回 pred['score'] →
    出 info;parquet 缺失 / 无当日 / 匹配不足 → 诚实退化纯 LGB(pred 不变)。
**命门**:全程只读 parquet(离线 GPU 批算产出),绝不在此跑模型 —— 这些测试不碰 GPU。
"""
import numpy as np
import pandas as pd
import pytest

from guanlan_v2.strategy.compute.v4_fincast import b3_mix_scores, apply_fincast_ensemble


def _codes(n):
    return [f"SZ{600000 + i:06d}" for i in range(n)]


def test_no_fincast_degrades_to_pure_lgb():
    """FinCast 全 NaN → 退化纯 LGB:score 原样、active=False。"""
    idx = _codes(80)
    lgb = pd.Series(np.linspace(1, 0, 80), index=idx)
    fc = pd.Series(np.nan, index=idx)
    mixed, info = b3_mix_scores(lgb, fc)
    pd.testing.assert_series_equal(mixed, lgb)
    assert info["active"] is False
    assert info["n_has_fc"] == 0


def test_few_matches_degrades():
    """匹配 <50 只(默认阈值)→ 退化纯 LGB。"""
    idx = _codes(80)
    lgb = pd.Series(np.linspace(1, 0, 80), index=idx)
    fc = pd.Series(np.nan, index=idx)
    fc.iloc[:30] = np.random.RandomState(0).randn(30)   # 仅 30 只有 FinCast
    mixed, info = b3_mix_scores(lgb, fc)
    pd.testing.assert_series_equal(mixed, lgb)
    assert info["active"] is False
    assert info["n_has_fc"] == 30


def test_default_weight_mix_formula():
    """≥50 匹配、默认权重 → mixed = 0.6·z(lgb) + 0.4·z(fincast)(逐位核对)。"""
    idx = _codes(60)
    rs = np.random.RandomState(42)
    lgb = pd.Series(rs.randn(60), index=idx)
    fc = pd.Series(rs.randn(60), index=idx)
    mixed, info = b3_mix_scores(lgb, fc)
    assert info["active"] is True
    assert abs(info["w_fc"] - 0.4) < 1e-12 and abs(info["w_lgb"] - 0.6) < 1e-12
    z = lambda s: (s - s.mean()) / (s.std() + 1e-9)
    expect = 0.6 * z(lgb) + 0.4 * z(fc)
    pd.testing.assert_series_equal(mixed, expect, check_names=False)


def test_adaptive_weight_is_clipped():
    """传入自适应 w_fc 越界 → 夹到 [0.1, 0.5]。"""
    idx = _codes(60)
    rs = np.random.RandomState(1)
    lgb = pd.Series(rs.randn(60), index=idx)
    fc = pd.Series(rs.randn(60), index=idx)
    _, hi = b3_mix_scores(lgb, fc, w_fc=0.9)
    _, lo = b3_mix_scores(lgb, fc, w_fc=-0.3)
    assert abs(hi["w_fc"] - 0.5) < 1e-12
    assert abs(lo["w_fc"] - 0.1) < 1e-12


def test_zscore_degenerate_lgb_no_crash():
    """LGB 全相等(std=0)→ z(lgb) 全 0,mixed = w_fc·z(fc),不崩不出 Inf/NaN。"""
    idx = _codes(60)
    lgb = pd.Series(5.0, index=idx)            # 常数
    fc = pd.Series(np.random.RandomState(2).randn(60), index=idx)
    mixed, info = b3_mix_scores(lgb, fc)
    assert info["active"] is True
    assert np.isfinite(mixed.values).all()


def test_mix_can_change_ranking():
    """与 LGB 无关的 FinCast 以 40% 权重混入 → 整体排名相对纯 LGB 改变(B3 真在影响排序)。
    注:B3 刻意 LGB 主导(w_fc≤0.4),故"完全反向、等幅"的对称腿只会压缩成 0.2·z(lgb) 不改序;
    真正改序需 FinCast 与 LGB 非共线。"""
    idx = _codes(60)
    rs = np.random.RandomState(11)
    lgb = pd.Series(np.linspace(1, 0, 60), index=idx)     # 单调
    fc = pd.Series(rs.randn(60), index=idx)               # 与 LGB 无关的 FinCast
    mixed, info = b3_mix_scores(lgb, fc)
    assert info["active"] is True
    assert not mixed.rank().equals(lgb.rank())            # 排名向量被改变


def _make_pred(codes, lgb_scores):
    """构造 build_v4 风格 pred:MultiIndex (instrument, datetime),含 'score' 列。"""
    d = pd.Timestamp("2026-03-10")
    idx = pd.MultiIndex.from_tuples([(c, d) for c in codes], names=["instrument", "datetime"])
    return pd.DataFrame({"score": lgb_scores}, index=idx)


def test_apply_reads_parquet_and_mixes(tmp_path):
    """apply_fincast_ensemble:只读 parquet → 当日匹配 ≥50 → 写回 pred['score'] 为混合分,active。"""
    codes = _codes(60)
    pred = _make_pred(codes, np.random.RandomState(7).randn(60))
    orig = pred["score"].copy()
    # 合成 FinCast parquet(扁平列 eval_date/instrument/pred_ret_5d)
    fc_df = pd.DataFrame({
        "eval_date": ["2026-03-10"] * 60,
        "instrument": codes,
        "pred_ret_5d": np.random.RandomState(8).randn(60),
    })
    p = tmp_path / "v4_fincast_pred.parquet"
    fc_df.to_parquet(p, index=False)
    info = apply_fincast_ensemble(pred, pd.Timestamp("2026-03-10"), str(p))
    assert info["active"] is True and info["n_has_fc"] == 60
    # pred['score'] 已被原地替换为混合分(与原 LGB 不再逐位相等)
    assert not np.allclose(pred["score"].values, orig.values)


def test_apply_missing_date_degrades(tmp_path):
    """parquet 存在但无当日预测 → 诚实退化:pred 不变、active=False、reason 说明。"""
    codes = _codes(60)
    pred = _make_pred(codes, np.random.RandomState(7).randn(60))
    orig = pred["score"].copy()
    fc_df = pd.DataFrame({
        "eval_date": ["2026-02-01"] * 60,     # 不同日
        "instrument": codes,
        "pred_ret_5d": np.random.RandomState(8).randn(60),
    })
    p = tmp_path / "v4_fincast_pred.parquet"
    fc_df.to_parquet(p, index=False)
    info = apply_fincast_ensemble(pred, pd.Timestamp("2026-03-10"), str(p))
    assert info["active"] is False
    pd.testing.assert_series_equal(pred["score"], orig)


def test_apply_missing_file_degrades():
    """parquet 文件不存在 → 诚实退化纯 LGB,不抛。"""
    codes = _codes(60)
    pred = _make_pred(codes, np.random.RandomState(7).randn(60))
    orig = pred["score"].copy()
    info = apply_fincast_ensemble(pred, pd.Timestamp("2026-03-10"), "G:/guanlan-v2/var/__no_such_fincast__.parquet")
    assert info["active"] is False
    pd.testing.assert_series_equal(pred["score"], orig)
