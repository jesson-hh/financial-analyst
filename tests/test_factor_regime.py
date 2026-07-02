# tests/test_factor_regime.py
# regime 层命门:截断不变性(PIT)/ 快照缓存等价 / 热身诚实缺席 / 中性恒等 / 倾斜夹逼。
import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import factor_regime as FR
from guanlan_v2.strategy.compute.factor_regime import (apply_regime_weights,
                                                       regime_features,
                                                       walk_forward_regimes)


def _feat(n=700, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    ls = pd.Series(rng.normal(0.001, 0.01, n), index=idx)
    ls.iloc[n // 2:] -= 0.004          # 后半段下移 → 两个可辨 regime
    return regime_features(ls)


def test_truncation_invariance():
    # PIT 守护:删未来数据重跑,历史 regime 逐位不变(参数与状态都只依赖 ≤t)。
    feat = _feat()
    full, _ = walk_forward_regimes(feat, warmup=200, refit_every=21)
    part, _ = walk_forward_regimes(feat.iloc[:520], warmup=200, refit_every=21)
    assert len(part) > 0
    pd.testing.assert_frame_equal(full.iloc[:len(part)].reset_index(drop=True),
                                  part.reset_index(drop=True))


def test_snapshot_cache_equivalence():
    # 缓存复用(regen 快路径)与冷算逐位一致。
    feat = _feat()
    cold, snaps = walk_forward_regimes(feat, warmup=200, refit_every=21)
    cache = {sn["fit_asof"]: sn for sn in snaps}
    warm, _ = walk_forward_regimes(feat, warmup=200, refit_every=21, snapshot_cache=cache)
    pd.testing.assert_frame_equal(cold.reset_index(drop=True), warm.reset_index(drop=True))


def test_warmup_honest_absence_and_pfav_range():
    feat = _feat()
    empty, _ = walk_forward_regimes(feat.iloc[:100], warmup=200)
    assert empty.empty                                     # 热身不足 → 不出行
    df, _ = walk_forward_regimes(feat, warmup=200, refit_every=21)
    assert df["p_fav"].between(0.0, 1.0).all()
    yrs = len(df) / 244.0
    assert (df["state"].diff().abs().sum() / yrs) <= 3.0   # λ 定标后切换有界(宽松护栏)


def test_apply_regime_weights_neutral_identity_and_clip():
    sup = [("f1", 1.0), ("f2", 2.0)]
    fam_of = {"f1": "技术", "f2": "波动率"}
    out, info = apply_regime_weights(sup, fam_of, {"技术": 0.5, "波动率": 1.0},
                                     {"技术", "波动率"})
    assert out[0][1] == pytest.approx(1.0)          # p=0.5 → 中性不动(w_eff≡w)
    assert out[1][1] == pytest.approx(2.0 * 1.25)   # p=1 → tilt=1.5 → 乘子封顶 1.25
    out2, _ = apply_regime_weights(sup, fam_of, {"技术": 0.0}, {"技术"})
    assert out2[0][1] == pytest.approx(0.75)        # p=0 → 乘子地板 0.75
    assert out2[1][1] == pytest.approx(2.0)         # 未激活族原样
    assert info[0]["family"] == "技术" and "w_eff" in info[0]


import json


def _write_ls(tmp_path, monkeypatch, n=700):
    # 构造族 L/S 产物(经 factor_ls 模块常量 monkeypatch)
    from guanlan_v2.strategy.compute import factor_ls as FL
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "ls.parquet")
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2022-01-03", periods=n)
    ls = rng.normal(0.001, 0.01, n)
    ls[n // 2:] -= 0.004
    df = pd.DataFrame({"date": idx, "family": "技术", "factor_id": "f1",
                       "ls_ret": ls,
                       "available_date": idx.shift(1)})
    df.to_parquet(tmp_path / "ls.parquet", index=False)


def test_build_factor_regime_products_and_hindsight(tmp_path, monkeypatch):
    _write_ls(tmp_path, monkeypatch)
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    monkeypatch.setattr(FR, "FACTOR_REGIME_META_JSON", tmp_path / "rg_meta.json")
    monkeypatch.setattr(FR, "WARMUP", 200)
    n = FR.build_factor_regime()
    assert n > 0
    df = pd.read_parquet(tmp_path / "rg.parquet")
    assert set(df["family"]) == {"技术"}
    assert {"p_fav", "state", "state_hindsight", "confirmed_since", "source"} <= set(df.columns)
    assert (df["source"] == "factor-regime-jm").all()
    meta = json.loads((tmp_path / "rg_meta.json").read_text(encoding="utf-8"))
    assert meta["spec_hash"] == FR.SPEC_HASH and meta["trials"] >= 36
    # 同 spec 复跑幂等:trials 不涨
    FR.build_factor_regime()
    meta2 = json.loads((tmp_path / "rg_meta.json").read_text(encoding="utf-8"))
    assert meta2["trials"] == meta["trials"]


def test_build_without_ls_honest(tmp_path, monkeypatch):
    from guanlan_v2.strategy.compute import factor_ls as FL
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "none.parquet")
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    assert FR.build_factor_regime() == 0               # 诚实缺席,不造数


def test_resolve_regime_weights_fallbacks_and_applied(tmp_path, monkeypatch):
    monkeypatch.setattr(FR, "FACTOR_REGIME_GATE_JSON", tmp_path / "gate.json")
    monkeypatch.setattr(FR, "FACTOR_REGIME_PARQUET", tmp_path / "rg.parquet")
    fx = [{"id": "fa_reversal", "w": 1.0}]              # catalog legacy:family=动量反转
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and b["applied"] is False and "闸产物缺失" in b["fallback_reason"]
    (tmp_path / "gate.json").write_text(
        json.dumps({"spec_hash": "WRONG", "activated": ["动量反转"]}), encoding="utf-8")
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and "陈闸" in b["fallback_reason"]
    (tmp_path / "gate.json").write_text(
        json.dumps({"spec_hash": FR.SPEC_HASH, "activated": []}), encoding="utf-8")
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and "0 族激活" in b["fallback_reason"]
    (tmp_path / "gate.json").write_text(
        json.dumps({"spec_hash": FR.SPEC_HASH, "activated": ["动量反转"]}), encoding="utf-8")
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert eff is None and "regime 产物缺失" in b["fallback_reason"]
    pd.DataFrame({"date": [pd.Timestamp("2026-07-01")], "family": ["动量反转"],
                  "p_fav": [1.0], "state": [1],
                  "confirmed_since": [pd.Timestamp("2026-06-20")]}
                 ).to_parquet(tmp_path / "rg.parquet", index=False)
    eff, b = FR.resolve_regime_weights(fx, "2026-07-01")
    assert b["applied"] is True and eff[0]["w"] == pytest.approx(1.25)   # p=1 封顶乘子
    eff, b = FR.resolve_regime_weights(fx, "2026-07-20")
    assert eff is None and "过期" in b["fallback_reason"]                 # 新鲜度 ≤3 交易日
