"""#1 因子中性化(行业 + 市值)— guanlan 层纯函数,逐日截面 OLS 残差。

为什么在 guanlan 层而非引擎层:report2 里对**原始因子做一次中性化、残差替代原值**后,
残差既流进 ``build_report``(报告 ic/分层/多空)又喂给外层 headline/ic_decay,两处口径
天然一致;引擎 ``build_report`` 内部中性化则外层 headline 仍用 raw 因子 → 内外不一致。
引擎 ``factors/eval/preprocess.py:neutralize`` 的 ``raise NotImplementedError`` 占位是
诚实的(声明未实现),保持不碰。

算法(Barra-lite 截面中性化,逐交易日独立):
  设计矩阵 X = [行业哑变量(one-hot,张成各自截距)] (+ [中心化 log 市值]);
  最小二乘 β = lstsq(X, factor);残差 = factor − Xβ 即中性化后因子(对行业/市值正交)。
诚实降级:
  · industry 全 None → 仅市值中性化(显式加常数列);mktcap 全缺 → 仅行业中性化;
  · 二者皆 None → 无可中性化项,原样返回(不去均值、不伪造);
  · 某日有效样本 n < 设计列数+2 或 < min_obs → 该日残差全 NaN(不可中性化的日子不伪造原值)。
PIT:逐日只用当日截面的行业/市值,不跨日,无前视。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def neutralize_factor(factor: pd.Series, industry=None, mktcap=None,
                      min_obs: int = 5, unknown_industry: str = "未知") -> pd.Series:
    """逐日截面行业 + 市值中性化,返回与 ``factor`` 同索引的残差 Series。

    参数
    ----
    factor : pd.Series
        (datetime, code) MultiIndex 的因子值。
    industry : pd.Series | None
        每 (datetime, code) 的行业标签(str)。None → 跳过行业项。
    mktcap : pd.Series | None
        每 (datetime, code) 的总市值(>0)。内部取 log 并中心化入回归。None / 全 NaN → 跳过市值项。
    min_obs : int
        某日做中性化所需的最少有效样本数(再叠加 n ≥ 设计列数+2 的自由度闸)。
    unknown_industry : str | None
        「未分类」哨兵标签(默认 "未知",对齐引擎 IndustryLoader.UNKNOWN_INDUSTRY)。带此标签的股票
        **不是真行业**:从行业回归中排除(残差 NaN),既不污染真行业组、也不被冒充已中性化。None → 不排除。
    """
    if not isinstance(factor, pd.Series):
        raise TypeError("factor 必须是 pd.Series")
    # 无可中性化项,或非截面(单层)索引 → 无法逐日截面回归,原样返回。
    if industry is None and mktcap is None:
        return factor.copy()
    if not isinstance(factor.index, pd.MultiIndex):
        return factor.copy()

    log_mv = None
    if mktcap is not None:
        mv = pd.to_numeric(pd.Series(mktcap), errors="coerce").reindex(factor.index)
        with np.errstate(invalid="ignore", divide="ignore"):
            log_mv = np.log(mv.where(mv > 0))
    ind = pd.Series(industry).reindex(factor.index) if industry is not None else None

    def _resid_one(sub: pd.Series) -> pd.Series:
        idx = sub.index
        yv = sub.to_numpy(dtype=float)
        fin = np.isfinite(yv)

        # 行业列:至少一个有效(非 None / 非 NaN / 非「未知」哨兵)标签才用。
        use_ind = False
        labels = None
        lab_valid = None
        if ind is not None:
            labels = ind.reindex(idx).to_numpy()
            # 有效行业标签 = 非 None / 非 NaN / 非「未知」哨兵(未分类股不进行业回归,诚实排除)
            lab_valid = np.array([(l is not None) and (l == l) and (l != unknown_industry)
                                  for l in labels])
            if lab_valid.any():
                use_ind = True

        # 回归候选行 = 因子有限 ∩(行业有效,若用行业)。市值覆盖率须按【候选行】判,
        # 而非含 NaN-因子行的全截面(否则 NaN-因子行的市值会虚高覆盖率,误纳市值致回归样本骤减)。
        candidate = fin.copy()
        if use_ind:
            candidate &= lab_valid

        # 市值列:候选行里有效 log 市值占比够大(≥半数且≥2 个、且取值非全同)才用。
        use_size = False
        s = None
        s_fin = None
        if log_mv is not None:
            s = log_mv.reindex(idx).to_numpy(dtype=float)
            s_fin = np.isfinite(s)
            cand_n = int(candidate.sum())
            sc = s_fin & candidate
            if cand_n > 0 and int(sc.sum()) >= max(2, cand_n // 2) and np.unique(s[sc]).size >= 2:
                use_size = True

        if not use_size and not use_ind:
            return pd.Series(np.nan, index=idx)

        valid = candidate.copy()
        if use_size:
            valid &= s_fin
        vi = np.where(valid)[0]
        nv = vi.size
        if nv == 0:
            return pd.Series(np.nan, index=idx)

        cols = []
        if use_ind:
            lv = labels[vi]
            uniq = pd.unique(lv)
            cols.append((lv[:, None] == uniq[None, :]).astype(float))  # one-hot 张成截距
        else:
            cols.append(np.ones((nv, 1)))                              # 仅市值时显式常数
        if use_size:
            sz = s[vi].astype(float)
            cols.append((sz - sz.mean()).reshape(-1, 1))               # 中心化 log 市值
        X = np.concatenate(cols, axis=1)
        ncol = X.shape[1]
        if nv < ncol + 2 or nv < min_obs:
            return pd.Series(np.nan, index=idx)   # 自由度不足 → 该日诚实 NaN

        beta, *_ = np.linalg.lstsq(X, yv[vi], rcond=None)
        resid = yv[vi] - X @ beta
        out_s = pd.Series(np.nan, index=idx)
        out_s.iloc[vi] = resid
        return out_s

    # 逐日残差按组拼接(非 .loc 标签赋值)→ 对重复 (datetime, code) 索引也鲁棒;再对齐回原索引顺序。
    parts = [_resid_one(sub) for _, sub in factor.groupby(level=0, sort=False)]
    out = pd.concat(parts) if parts else pd.Series(dtype=float, index=factor.index)
    return out.reindex(factor.index)
