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


def _block_shuffle(s: pd.Series, rng, block: int = PLACEBO_BLOCK) -> pd.Series:
    """时间块打乱(保边际分布与块内自相关)——安慰剂臂料(嫁接自评审)。"""
    v = s.values
    blocks = [v[i:i + block] for i in range(0, len(v), block)]
    order = rng.permutation(len(blocks))
    out = np.concatenate([blocks[j] for j in order])[: len(v)]
    return pd.Series(out, index=s.index)


def _delta(a: List[Optional[float]], b: List[Optional[float]]) -> np.ndarray:
    x = np.array([np.nan if v is None else v for v in a], dtype=float)
    y = np.array([np.nan if v is None else v for v in b], dtype=float)
    d = x - y
    return d[np.isfinite(d)]


def gate_report(frames, close_wide, fams, regime_pfav, warmup_date,
                switch_stats: Optional[dict] = None, n_trials: Optional[int] = None,
                rng_seed: int = 0, n_placebo: int = N_PLACEBO,
                placebo_block: int = PLACEBO_BLOCK) -> dict:
    """全指标闸报告(纯、无时间戳 → 幂等;可注入合成数据自证)。
    switch_stats={family:{switch_per_year, agree_hindsight}}(生产由 run_gate 从
    regime 产物算好传入;None=跳过 whipsaw 护栏,仅合成测试用)。"""
    from guanlan_v2.strategy.compute.cpcv import _norm_cdf, deflated_sharpe, make_splits

    res = eval_arms(frames, close_wide, fams, regime_pfav, warmup_date)
    families = sorted(res["ic_fam"])
    out_fam: Dict[str, dict] = {}
    pvals: Dict[str, Optional[float]] = {}
    for fam in families:
        d = _delta(res["ic_fam"][fam], res["ic_static"])
        t = nw_tstat(d)
        p = (1.0 - _norm_cdf(t)) if t is not None else None
        pvals[fam] = p
        out_fam[fam] = {"n_rb": int(len(d)),
                        "d_ic_mean": (float(d.mean()) if len(d) else None),
                        "nw_t": t, "p": p}
    survivors = bh_fdr(pvals)

    # 全族臂 Δ + 安慰剂(block-shuffle p_fav;真臂须显著优于安慰剂——归因,spec §7 #5)
    d_all = _delta(res["ic_all"], res["ic_static"])
    real_all = float(d_all.mean()) if len(d_all) else None
    rng = np.random.default_rng(rng_seed)
    plac = []
    for _ in range(int(n_placebo)):
        shuf = {f: _block_shuffle(s, rng, placebo_block)
                for f, s in regime_pfav.items()}
        r2 = eval_arms(frames, close_wide, fams, shuf, warmup_date, fam_arms=False)
        dd = _delta(r2["ic_all"], r2["ic_static"])
        plac.append(float(dd.mean()) if len(dd) else np.nan)
    plac = np.array(plac, dtype=float)
    plac = plac[np.isfinite(plac)]
    placebo_t = None
    if real_all is not None and len(plac) >= 5 and plac.std(ddof=1) > 0:
        placebo_t = float((real_all - plac.mean()) / plac.std(ddof=1))

    # 代理池 do-no-harm(spec §7 #6,评审必做:闸认证与生产 blend 作用面同总体)
    d_pool = _delta(res["pool_all"], res["pool_static"])
    pool_d_ic = float(d_pool.mean()) if len(d_pool) else None

    # CPCV 折块(spec §7 #3):walk-forward Δ 序列按 make_splits test 折切块 → 路径分布
    d_by_date = {}
    for d_, a_, b_ in zip(res["dates"], res["ic_all"], res["ic_static"]):
        if a_ is not None and b_ is not None:
            d_by_date[d_] = a_ - b_
    paths = make_splits(res["dates"], n_groups=6, k=2, purge=HORIZON + 1, embargo=5)
    path_means = []
    for _tr, te in paths:
        vals = [d_by_date[d_] for d_ in te if d_ in d_by_date]
        if len(vals) >= 10:
            path_means.append(float(np.mean(vals)))
    cpcv_median = float(np.median(path_means)) if path_means else None
    cpcv_p05 = float(np.percentile(path_means, 5)) if path_means else None

    # DSR(spec §7 #4):动态全族臂 top-decile 多头超额(未年化,decile_metrics 口径)
    nt = int(n_trials if n_trials is not None else max(36, _resolve_trials()))
    dsr = deflated_sharpe([v for v in res["ls_all"] if v is not None], n_trials=nt)

    # 延迟敏感性(报告性,spec §7 末行):p_fav 滞后 20 交易日的 Δ
    lag_pfav = {f: s.shift(20) for f, s in regime_pfav.items()}
    r3 = eval_arms(frames, close_wide, fams, lag_pfav, warmup_date, fam_arms=False)
    d_lag = _delta(r3["ic_all"], r3["ic_static"])
    delay20_d_ic = float(d_lag.mean()) if len(d_lag) else None

    activated = []
    for fam in families:
        f = out_fam[fam]
        ok = (f["d_ic_mean"] is not None and f["d_ic_mean"] >= GATE_MIN_DIC
              and f["nw_t"] is not None and f["nw_t"] >= GATE_MIN_T
              and fam in survivors
              and placebo_t is not None and placebo_t >= 2.0
              and pool_d_ic is not None and pool_d_ic >= 0.0
              and cpcv_median is not None and cpcv_median > 0.0
              and cpcv_p05 is not None and cpcv_p05 > -0.005
              and dsr is not None and dsr >= GATE_DSR)
        if ok and switch_stats is not None:
            ss = switch_stats.get(fam) or {}
            ok = (ss.get("switch_per_year") is not None
                  and ss["switch_per_year"] <= GATE_MAX_SWITCH
                  and ss.get("agree_hindsight") is not None
                  and ss["agree_hindsight"] >= GATE_MIN_AGREE)
        f["bh_survive"] = fam in survivors
        f["pass"] = bool(ok)
        if ok:
            activated.append(fam)
    return {"spec_hash": SPEC_HASH, "n_trials": nt, "n_rb": len(res["dates"]),
            "families": out_fam,
            "global": {"d_ic_all": real_all, "placebo_t": placebo_t,
                       "placebo_mean": (float(plac.mean()) if len(plac) else None),
                       "pool_d_ic": pool_d_ic, "cpcv_median": cpcv_median,
                       "cpcv_p05": cpcv_p05, "cpcv_paths": len(path_means),
                       "dsr": dsr, "delay20_d_ic": delay20_d_ic},
            "switch_stats": switch_stats, "activated": activated,
            "passes_gate": bool(activated),
            "note": "passes 仅建议;闸只由人工 CLI 触发=人工确认;0 族过闸=合法结局。"}


def _switch_stats(rg: pd.DataFrame, warmup_date) -> dict:
    """whipsaw 护栏料:OOS 年均切换 + 与 hindsight 吻合率(hindsight 仅在此处消费)。"""
    out = {}
    for fam, g in rg.groupby("family"):
        g = g[g["date"] >= pd.Timestamp(warmup_date)].sort_values("date")
        if len(g) < 50:
            out[fam] = {"switch_per_year": None, "agree_hindsight": None}
            continue
        st = g["state"].to_numpy()
        sw = float((st[1:] != st[:-1]).sum()) / max(len(g) / 244.0, 1e-9)
        agree = None
        if "state_hindsight" in g.columns and g["state_hindsight"].notna().any():
            hh = g.dropna(subset=["state_hindsight"])
            agree = float((hh["state"] == hh["state_hindsight"]).mean())
        out[fam] = {"switch_per_year": sw, "agree_hindsight": agree}
    return out


def run_gate(universe: str = "csi800", start: str = "2016-01-01",
             end: Optional[str] = None) -> dict:
    """生产闸(人工 CLI 触发;重:物化因子框 + 20 次安慰剂重评,预计 30-60min)。"""
    from guanlan_v2.strategy.compute.factor_ls import materialize_factor_frames
    from guanlan_v2.strategy.paths import FACTOR_REGIME_PARQUET

    frames, close_wide, fams = materialize_factor_frames(universe, start, end)
    rg = pd.read_parquet(FACTOR_REGIME_PARQUET)
    regime_pfav = {str(fam): pd.Series(g["p_fav"].values,
                                       index=pd.DatetimeIndex(g["date"])).sort_index()
                   for fam, g in rg.groupby("family")}
    warmup_date = min(s.index.min() for s in regime_pfav.values())
    rep = gate_report(frames, close_wide, fams, regime_pfav, warmup_date,
                      switch_stats=_switch_stats(rg, warmup_date))
    rep["asof"] = str(pd.Timestamp(rg["date"].max()).date())
    rep["universe"], rep["start"] = universe, start
    tmp = str(FACTOR_REGIME_GATE_JSON) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    os.replace(tmp, str(FACTOR_REGIME_GATE_JSON))
    return rep


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="regime 激活闸(人工触发=人工确认)")
    ap.add_argument("--universe", default="csi800")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    rep = run_gate(a.universe, a.start, a.end)
    brief = {"activated": rep["activated"], "asof": rep.get("asof"),
             "global": rep["global"],
             "families": {k: {kk: v.get(kk) for kk in ("d_ic_mean", "nw_t", "pass")}
                          for k, v in rep["families"].items()}}
    print(json.dumps(brief, ensure_ascii=False, indent=1, default=str))
