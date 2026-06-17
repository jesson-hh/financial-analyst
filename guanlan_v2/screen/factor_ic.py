# -*- coding: utf-8 -*-
"""因子库**实测 IC**:对 catalog 全部因子算近窗逐日截面 rank-IC,落 ``factor_ic.parquet``。

替掉静态"展示 IC"装饰数(审计确认的 mock):/screen/factors 合并本产物下发真 IC,
前端因子卡显示「实测 RankIC·近60日·沪深300」。

口径(与 workflow `_rank_ic_series` 同思想,独立小实现避免拖 5k 行模块):
- 截面 = csi300(快、流动性好、代表性够;~300 码 × ~470 自然日面板,秒级到分钟级);
- 每个交易日 t:spearman( factor_t 截面, 未来 horizon 日简单收益截面 );取最近 ``days`` 个有效日;
- ``ic`` = 逐日 IC 均值,``icir`` = 均值/标准差(日频,不年化),``n_days`` = 有效天数;
- 表达式已预定向 → ic>0 即"按目录方向有效";算不出(列缺/全 NaN)→ 该因子**不出行**(诚实缺席,
  前端显示「—」),不填装饰数。

跑法:regen 子进程顺带(见 compute/regen.py step factor_ic);或手动
``python -c "from guanlan_v2.screen.factor_ic import compute_catalog_ic; compute_catalog_ic()"``。
写盘原子(.tmp → os.replace),与三产物同范式。
"""
from __future__ import annotations

import os
from typing import Optional

from guanlan_v2.strategy.paths import ARTIFACTS_DIR

FACTOR_IC_PARQUET = ARTIFACTS_DIR / "factor_ic.parquet"


def compute_catalog_ic(universe: str = "csi300", days: int = 60, horizon: int = 5,
                       end: Optional[str] = None) -> int:
    """全 catalog 实测 rank-IC → factor_ic.parquet。返回成功计算的因子数。"""
    import pandas as pd
    from datetime import date, timedelta

    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.data.universe import resolve_universe_codes
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached

    from guanlan_v2.screen.catalog import FACTOR_DEFS

    end_d = date.fromisoformat(end) if end else date.today()
    # 470 自然日 ≈ 240 交易日热身(目录最长窗 ts_max(close,240))+ 60 日 IC 窗 + 余量
    start = (end_d - timedelta(days=470)).isoformat()
    end_s = end_d.isoformat()

    codes = [str(c) for c in resolve_universe_codes(universe)]
    loader = get_default_loader()
    panel = load_panel_cached(loader, codes, start, end_s, freq="day")

    # 大盘因子参照:注入 idx_ret(真沪深300;复用 workflow 注入器,不 in-place 改缓存面板)
    try:
        from guanlan_v2.workflow.api import _inject_market_refs
        panel, _w = _inject_market_refs(panel, "csi300", None, start, end_s, freq="day")
        for _msg in (_w or []):
            print(f"[factor_ic] 警告: {_msg}")   # 指数源停更 → 共振族尾窗缺数,regen 日志显形(P0③)
    except Exception:  # noqa: BLE001
        pass  # 注入失败 → 共振/跟随族算不出、诚实缺席,其余族不受影响

    close = compile_factor("close")(panel)
    if close is None or not isinstance(close, pd.Series):
        raise RuntimeError("factor_ic: 面板无 close,无法算前瞻收益")
    # 未来 horizon 日简单收益(按 code 分组 shift,不跨票)
    fwd = close.groupby(level="code").shift(-horizon) / close - 1.0

    rows = []
    for fid, meta in FACTOR_DEFS.items():
        expr = meta.get("expr")
        if not expr:
            continue
        try:
            fac = compile_factor(expr)(panel)
            if fac is None or not isinstance(fac, pd.Series):
                continue
            df = pd.DataFrame({"f": fac, "r": fwd}).dropna()
            if df.empty:
                continue
            dts = df.index.get_level_values("datetime")
            uniq = sorted(pd.Index(dts).unique())[-int(days):]
            _dir = float(meta.get("dir", 1) or 1)   # 按目录方向折算(legacy fa_distrib=-1 的 expr 未预定向)
            ics = []
            for t in uniq:
                sub = df[dts == t]
                if len(sub) >= 30:  # 截面太薄不算(防噪声 IC)
                    ic_t = sub["f"].rank().corr(sub["r"].rank())
                    if pd.notna(ic_t):
                        ics.append(_dir * float(ic_t))
            if len(ics) < 10:  # 有效天数太少 → 诚实缺席
                continue
            s = pd.Series(ics)
            rows.append({
                "id": fid, "short": meta["short"], "family": meta["family"],
                "ic": float(s.mean()),
                "icir": float(s.mean() / s.std()) if float(s.std()) > 0 else None,
                "n_days": int(len(ics)), "asof": end_s,
            })
        except Exception:  # noqa: BLE001
            continue  # 单因子失败不拖累全表(诚实缺席)

    out = pd.DataFrame(rows)
    tmp = str(FACTOR_IC_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(FACTOR_IC_PARQUET))
    return len(out)


def load_factor_ic() -> dict:
    """读实测 IC 产物 → {id: {ic, icir, n_days, asof}};缺文件 → {}(前端显示「—」)。"""
    import pandas as pd
    if not FACTOR_IC_PARQUET.exists():
        return {}
    try:
        df = pd.read_parquet(FACTOR_IC_PARQUET)
        return {str(r["id"]): {"ic": (None if pd.isna(r["ic"]) else float(r["ic"])),
                               "icir": (None if pd.isna(r.get("icir")) else float(r["icir"])),
                               "n_days": int(r["n_days"]), "asof": str(r["asof"])}
                for _, r in df.iterrows()}
    except Exception:  # noqa: BLE001
        return {}
