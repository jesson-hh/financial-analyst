# -*- coding: utf-8 -*-
"""regime 激活闸:walk-forward ΔrankIC 主判据 + CPCV 折块辅 + BH-FDR + 安慰剂 + 代理池 + whipsaw。

纪律复刻 cpcv.validate_dl_source 先例(GAT 全市场 −0.029 拒 / csi1000 +0.254 激活同一套):
- 闸只由人工 CLI 触发(regen 绝不自动跑)→ activated 落盘即人工确认动作;
- 0 族过闸 = 合法交付(结论:该范式在本仓因子上无 OOS 增量);
- 所有臂共用 walk-forward PIT 的 p_fav(逐日真 OOS);CPCV 档 = 按 make_splits test 折
  切块统计 Δ 分布(免「非连续 train 折上重拟切换罚模型」的统计不合法操作——评审镜头2)。
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.factor_regime import (ETA, SPEC_HASH, TILT_HI,
                                                       TILT_LO, _resolve_trials)
from guanlan_v2.strategy.paths import FACTOR_REGIME_GATE_JSON

GATE_MIN_DIC = 0.005      # walk-forward mean ΔrankIC 门槛(spec §7 #1)
GATE_MIN_T = 2.0          # Newey-West t 门槛
GATE_Q = 0.10             # BH-FDR(spec §7 #2)
GATE_DSR = 0.5            # 同 cpcv.DL_GATE_DSR(spec §7 #4)
GATE_MAX_SWITCH = 2.0     # OOS 年均切换上限(spec §7 #7)
GATE_MIN_AGREE = 0.70     # 与 hindsight 状态吻合率下限
HORIZON = 5               # 非重叠换仓步长(与 factor_ic/strict_validate 口径一致)
N_PLACEBO = 20
PLACEBO_BLOCK = 63        # 季度块 shuffle(保自相关)
POOL_TOP = 200            # 代理候选池(近似生产 blend 真实作用面,评审必做修补)


def nw_tstat(x, lag: int = 5) -> Optional[float]:
    """Newey-West(Bartlett 核)均值 t;n<8 或方差退化 → None(诚实缺席)。"""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 8:
        return None
    e = x - x.mean()
    s = float(e @ e) / n
    for j in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - j / (lag + 1.0)) * float(e[j:] @ e[:-j]) / n
    if s <= 0:
        return None
    return float(x.mean() / np.sqrt(s / n))


def bh_fdr(pvals: Dict[str, Optional[float]], q: float = GATE_Q) -> set:
    """Benjamini-Hochberg:返回存活 key 集(单边 p;None/NaN 不参与)。"""
    items = sorted((p, k) for k, p in pvals.items()
                   if p is not None and np.isfinite(p))
    m = len(items)
    thr = 0
    for i, (p, _k) in enumerate(items, start=1):
        if p <= q * i / m:
            thr = i
    return {k for _p, k in items[:thr]}


def _zscore_cs(row: pd.Series) -> pd.Series:
    v = row.dropna()
    sd = v.std(ddof=0)
    if len(v) < 30 or not np.isfinite(sd) or sd <= 0:
        return pd.Series(dtype=float)
    return (v - v.mean()) / sd


def _rank_ic(score: pd.Series, ret: pd.Series) -> Optional[float]:
    df = pd.DataFrame({"s": score, "r": ret}).dropna()
    if len(df) < 30:
        return None
    ic = df["s"].rank().corr(df["r"].rank())
    return None if pd.isna(ic) else float(ic)


def eval_arms(frames: Dict[str, pd.DataFrame], close_wide: pd.DataFrame,
              fams: Dict[str, str], regime_pfav: Dict[str, pd.Series],
              warmup_date, horizon: int = HORIZON, pool_top: int = POOL_TOP,
              fam_arms: bool = True) -> dict:
    """核心引擎(纯,可注入合成数据):非重叠换仓日上算三类复合 rankIC——
    静态(等权基线)/ 逐族动态(仅该族倾斜,归因用)/ 全族动态;另出代理池(静态复合
    top-N 内)口径与动态 top-decile 多头超额(DSR 料)。regime_pfav 取「最后一个 ≤t」行
    (序列本身来自 walk-forward,PIT 已保证)。"""
    cw = close_wide.sort_index()
    fwd = cw.shift(-horizon) / cw - 1.0
    dates = [d for d in cw.index if d >= pd.Timestamp(warmup_date)]
    rb = dates[::horizon]
    families = sorted(set(fams.values()))
    res = {"dates": [], "ic_static": [], "ic_all": [], "ls_all": [],
           "ic_fam": {f: [] for f in families},
           "pool_static": [], "pool_all": []}
    for t in rb:
        if t not in fwd.index:
            continue
        r = fwd.loc[t]
        if r.dropna().empty:
            continue
        zs = {}
        for fid, fw_ in frames.items():
            if t in fw_.index:
                z = _zscore_cs(fw_.loc[t])
                if len(z):
                    zs[fid] = z
        if not zs:
            continue

        def _composite(weights: Dict[str, float]) -> pd.Series:
            wsum = sum(abs(w) for w in weights.values()) or 1.0
            acc = None
            for fid, z in zs.items():
                part = z * (weights.get(fid, 0.0) / wsum)
                acc = part if acc is None else acc.add(part, fill_value=0.0)
            return acc

        def _tilt_w(only_fam: Optional[str]) -> Dict[str, float]:
            w = {}
            for fid in zs:
                fam = fams[fid]
                p = None
                p_s = regime_pfav.get(fam)
                if p_s is not None:
                    sub = p_s.loc[:t].dropna()
                    p = float(sub.iloc[-1]) if len(sub) else None
                if p is None or (only_fam is not None and fam != only_fam):
                    w[fid] = 1.0
                else:
                    tilt = min(max(2.0 * p, TILT_LO), TILT_HI)
                    w[fid] = (1.0 - ETA) + ETA * tilt
            return w

        c_static = _composite({fid: 1.0 for fid in zs})
        ic_s = _rank_ic(c_static, r)
        if ic_s is None:
            continue
        c_all = _composite(_tilt_w(None))
        res["dates"].append(t)
        res["ic_static"].append(ic_s)
        res["ic_all"].append(_rank_ic(c_all, r))
        n_dec = max(1, int(len(c_all) * 0.1))
        top_d = c_all.sort_values(ascending=False).head(n_dec).index
        ls_v = float(r.reindex(top_d).mean() - r.reindex(c_all.index).mean())
        res["ls_all"].append(ls_v if np.isfinite(ls_v) else None)
        if fam_arms:
            for fam in families:
                res["ic_fam"][fam].append(_rank_ic(_composite(_tilt_w(fam)), r))
            top = c_static.sort_values(ascending=False).head(pool_top).index
            res["pool_static"].append(_rank_ic(c_static.reindex(top), r.reindex(top)))
            res["pool_all"].append(_rank_ic(c_all.reindex(top), r.reindex(top)))
    return res
