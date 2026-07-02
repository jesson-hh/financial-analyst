# -*- coding: utf-8 -*-
"""因子族 regime 层:族 L/S 序列 → jump-penalty walk-forward → p_fav 连续概率 → 权重倾斜。

PIT 双保证:参数每 REFIT_EVERY 交易日 expanding(仅 ≤t)重拟;其间在线过滤;
守护测试 = 截断不变性(删未来历史逐位不变)。输出连续 p_fav,不出硬开关;
倾斜 w_eff = w·((1−η)+η·clip(2·p_fav, lo, hi)),η=0.5 → 有效乘子 ∈[0.75,1.25]
(AQR 3-0:倾斜必须保守、向静态收缩、设上限)。η/clip 为可审计常数,不许运行期调。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.jump_model import (dp_states, fit_jump_model,
                                                    online_state, soft_prob)
from guanlan_v2.strategy.paths import (FACTOR_REGIME_GATE_JSON,
                                       FACTOR_REGIME_META_JSON,
                                       FACTOR_REGIME_PARQUET)

ETA = 0.5
TILT_LO, TILT_HI = 0.5, 1.5
WARMUP = 500
REFIT_EVERY = 21
LAM_GRID = (50.0, 100.0, 200.0)
MAX_SWITCH_PER_YEAR = 1.5     # λ 定标目标(深研 3-0:jump ~0.8 vs HMM 2+)
FRESH_MAX_LAG = 3             # 产物新鲜度:asof 距排名日 ≤3 交易日(评审收紧)
_FEAT_COLS = ("feat_dvol", "feat_sortino20", "feat_sortino60", "feat_csv")
SPEC = {"eta": ETA, "tilt": [TILT_LO, TILT_HI], "warmup": WARMUP,
        "refit_every": REFIT_EVERY, "lam_grid": list(LAM_GRID), "k": 2,
        "features": list(_FEAT_COLS)}
SPEC_HASH = hashlib.md5(json.dumps(SPEC, sort_keys=True).encode()).hexdigest()[:10]


def regime_features(ls: pd.Series, csv: Optional[pd.Series] = None) -> pd.DataFrame:
    """族 L/S 序列 → 特征框(EWM 下行波动 hl=10 / EWM Sortino 20·60 / CSV 协变量)。
    全部 trailing EWM,t 行只含 ≤t 信息(PIT)。"""
    ls = ls.sort_index().astype(float)
    downside = ls.clip(upper=0.0)
    dvol = np.sqrt(downside.pow(2).ewm(halflife=10, min_periods=10).mean())

    def _sortino(hl: int) -> pd.Series:
        m = ls.ewm(halflife=hl, min_periods=hl).mean()
        d = np.sqrt(downside.pow(2).ewm(halflife=hl, min_periods=hl).mean())
        return m / (d + 1e-9)

    out = pd.DataFrame({"feat_dvol": dvol, "feat_sortino20": _sortino(20),
                        "feat_sortino60": _sortino(60)})
    if csv is not None and len(csv):
        out["feat_csv"] = csv.reindex(out.index).ffill()
    else:
        out["feat_csv"] = 0.0    # 无协变量 → 常数列(标准化后 z=0,不影响)
    return out.dropna()


def _pick_lambda(Xz: np.ndarray, lam_grid, seed: int):
    """λ 定标:取网格中(升序)首个「年切换 ≤MAX_SWITCH_PER_YEAR」的 λ(最灵敏且稳);
    都超 → 最大 λ 兜底。返回 (lam, centers, states, obj)。"""
    last = None
    for lam in sorted(lam_grid):
        C, s, obj = fit_jump_model(Xz, k=2, lam=lam, seed=seed)
        last = (float(lam), C, s, obj)
        yrs = max(len(Xz) / 244.0, 1e-9)
        if float((s[1:] != s[:-1]).sum()) / yrs <= MAX_SWITCH_PER_YEAR:
            return last
    return last


def walk_forward_regimes(feat: pd.DataFrame, warmup: int = WARMUP,
                         refit_every: int = REFIT_EVERY, lam_grid=LAM_GRID,
                         seed: int = 0, snapshot_cache: Optional[dict] = None):
    """逐日 PIT regime。i+1<warmup 不出行;每 refit_every 日 expanding 重拟
    (标准化 μσ 也只用 ≤t);其间 online_state 过滤。snapshot_cache({fit_asof: 快照})
    命中即免重拟(regen 快路径,等价性有测试守护)。
    返回 (df[date,p_fav,state,confirmed_since,fit_asof,lam], snapshots)。"""
    cols = [c for c in _FEAT_COLS if c in feat.columns]
    feat = feat[cols].sort_index()
    dates = list(feat.index)
    rows, snapshots = [], []
    cache = snapshot_cache or {}
    params = None
    prev_state: Optional[int] = None
    confirmed_since = None
    for i, t in enumerate(dates):
        if i + 1 < warmup:
            continue
        if params is None or (i + 1 - warmup) % refit_every == 0:
            asof = str(pd.Timestamp(t).date())
            sn = cache.get(asof)
            if sn and int(sn.get("n_obs", -1)) == i + 1:
                mu, sd = np.asarray(sn["mu"]), np.asarray(sn["sd"])
                C, lam = np.asarray(sn["centers"]), float(sn["lam"])
                fav, temp = int(sn["fav_state"]), float(sn["temp"])
                prev_state = int(sn["last_state"])
            else:
                hist = feat.iloc[: i + 1].values
                mu, sd = hist.mean(axis=0), hist.std(axis=0) + 1e-12
                lam, C, s_fit, obj = _pick_lambda((hist - mu) / sd, lam_grid, seed)
                fav = int(np.argmax(C[:, 1]))   # 标准化 sortino20 维更高的质心 = 有利态
                temp = max(obj / max(i + 1, 1), 1e-9)
                prev_state = int(s_fit[-1])
                sn = {"fit_asof": asof, "n_obs": i + 1, "lam": lam, "fav_state": fav,
                      "temp": temp, "last_state": prev_state,
                      "mu": mu.tolist(), "sd": sd.tolist(), "centers": C.tolist()}
            snapshots.append(sn)
            params = (mu, sd, C, lam, fav, temp, sn["fit_asof"])
        mu, sd, C, lam, fav, temp, fit_asof = params
        xz = (feat.iloc[i].values - mu) / sd
        st = online_state(xz, C, lam, prev_state)
        p = soft_prob(xz, C, lam, prev_state, temp)
        if confirmed_since is None or st != prev_state:
            confirmed_since = t                 # 先比 prev 再更新(状态连跑起点)
        prev_state = st
        rows.append({"date": t, "p_fav": float(p[fav]), "state": int(st == fav),
                     "confirmed_since": confirmed_since, "fit_asof": fit_asof,
                     "lam": float(lam)})
    return pd.DataFrame(rows), snapshots


def apply_regime_weights(sup: List[Tuple[str, float]], fam_of: Dict[str, str],
                         p_fav: Dict[str, float], activated: Set[str]):
    """纯函数:w_eff = w·((1−η)+η·clip(2·p_fav, lo, hi))。未激活族/无 p_fav → 原样。
    p_fav=0.5 → w_eff≡w(中性恒等,测试守护)。返回 (new_sup, per_factor 明细)。"""
    out, info = [], []
    for fid, w in sup:
        fam = fam_of.get(fid)
        p = p_fav.get(fam) if fam else None
        if fam in activated and p is not None:
            tilt = min(max(2.0 * float(p), TILT_LO), TILT_HI)
            w_eff = float(w) * ((1.0 - ETA) + ETA * tilt)
        else:
            w_eff = float(w)
        out.append((fid, w_eff))
        info.append({"id": fid, "family": fam, "w_user": float(w),
                     "w_eff": round(w_eff, 6),
                     "p_fav": (None if p is None else round(float(p), 4))})
    return out, info
