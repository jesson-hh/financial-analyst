# tests/test_regime_gate.py
# 激活闸门禁:NW-t 反对称 / BH 手算 / 中性零差 / 阳性对照必过 / 阴性对照必拒 / 幂等。
import json

import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import regime_gate as RG
from guanlan_v2.strategy.compute.regime_gate import bh_fdr, eval_arms, nw_tstat


def test_nw_tstat_basic():
    rng = np.random.default_rng(0)
    x = rng.normal(0.5, 1.0, 200)
    t = nw_tstat(x)
    assert t is not None and t > 3.0
    assert nw_tstat(-x) == pytest.approx(-t)          # 反对称
    assert nw_tstat(np.ones(20)) is None              # 零方差 → 诚实 None
    assert nw_tstat(x[:5]) is None                    # 样本太少 → None


def test_bh_fdr_hand_case():
    keep = bh_fdr({"a": 0.001, "b": 0.02, "c": 0.5}, q=0.10)
    assert keep == {"a", "b"}                         # 阈:0.0333/0.0667/0.1
    assert bh_fdr({"a": None, "b": 0.9}) == set()


def _synth(seed=0, n_days=420, n_codes=40):
    """阳性对照:famA 载荷在 regime=1 段正向计价、regime=0 段反向;famB 恒正向。
    返回 (frames, close_wide, fams, pfav_true)。100 日块交替,信号强(噪声极小)。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n_days + 1)
    codes = [f"C{i:02d}" for i in range(n_codes)]
    a, b = rng.normal(size=n_codes), rng.normal(size=n_codes)
    blocks = np.tile(np.repeat([1.0, 0.0], 100), n_days // 200 + 1)[:n_days]
    coefA = np.where(blocks > 0.5, 0.01, -0.01)
    ret = (coefA[:, None] * a[None, :] + 0.006 * b[None, :]
           + 0.0005 * rng.normal(size=(n_days, n_codes)))
    close = pd.DataFrame(
        100.0 * np.vstack([np.ones((1, n_codes)), np.exp(np.cumsum(ret, axis=0))]),
        index=idx, columns=codes)
    frames = {"fa": pd.DataFrame(np.tile(a, (n_days + 1, 1)), index=idx, columns=codes),
              "fb": pd.DataFrame(np.tile(b, (n_days + 1, 1)), index=idx, columns=codes)}
    fams = {"fa": "动量反转", "fb": "波动率"}
    pfav = {"动量反转": pd.Series(np.append(blocks, blocks[-1]), index=idx),
            "波动率": pd.Series(1.0, index=idx)}
    return frames, close, fams, pfav


def test_eval_arms_neutral_zero_delta():
    frames, close, fams, _ = _synth()
    pfav = {f: pd.Series(0.5, index=close.index) for f in set(fams.values())}
    res = eval_arms(frames, close, fams, pfav, close.index[0])
    assert res["ic_all"] == res["ic_static"]          # p=0.5 → 倾斜恒等 → 零差


def test_gate_positive_control_activates():
    # 阳性对照(闸自证):真 regime 依赖信号 → 动量反转族必过闸(闸不是永拒的橡皮闸)。
    frames, close, fams, pfav = _synth()
    rep = RG.gate_report(frames, close, fams, pfav, close.index[0],
                         switch_stats=None, n_trials=8, rng_seed=0)
    f = rep["families"]["动量反转"]
    assert f["d_ic_mean"] > RG.GATE_MIN_DIC and f["nw_t"] > RG.GATE_MIN_T
    assert f["bh_survive"] and "动量反转" in rep["activated"]
    assert rep["passes_gate"] is True
    assert rep["global"]["placebo_t"] is not None and rep["global"]["placebo_t"] >= 2.0
    assert rep["global"]["pool_d_ic"] is not None
    assert rep["global"]["cpcv_paths"] > 0 and rep["global"]["delay20_d_ic"] is not None


def test_gate_negative_control_rejects():
    # 阴性对照:p_fav 恒 0.5(无信息)→ 零差 → 全拒,activated 空(闸不是橡皮闸)。
    frames, close, fams, _ = _synth()
    pfav = {f: pd.Series(0.5, index=close.index) for f in set(fams.values())}
    rep = RG.gate_report(frames, close, fams, pfav, close.index[0],
                         switch_stats=None, n_trials=8, rng_seed=0)
    assert rep["activated"] == [] and rep["passes_gate"] is False


def test_gate_idempotent_same_seed():
    frames, close, fams, pfav = _synth()
    r1 = RG.gate_report(frames, close, fams, pfav, close.index[0], None, 8, 0)
    r2 = RG.gate_report(frames, close, fams, pfav, close.index[0], None, 8, 0)
    assert json.dumps(r1, sort_keys=True, default=str) == \
           json.dumps(r2, sort_keys=True, default=str)


def test_gate_whipsaw_guardrail_blocks():
    # 过闸族若 switch_stats 超限(年切换>2 或吻合率<0.7)→ 被护栏拦下。
    frames, close, fams, pfav = _synth()
    ss = {"动量反转": {"switch_per_year": 9.0, "agree_hindsight": 0.5},
          "波动率": {"switch_per_year": 9.0, "agree_hindsight": 0.5}}
    rep = RG.gate_report(frames, close, fams, pfav, close.index[0],
                         switch_stats=ss, n_trials=8, rng_seed=0)
    assert rep["activated"] == []
