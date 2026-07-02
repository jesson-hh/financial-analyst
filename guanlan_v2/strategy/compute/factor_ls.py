# -*- coding: utf-8 -*-
"""因子族多空(L/S)收益序列:regime 层地基产物(PIT:available_date = t+1)。

- 白名单 = 6 个纯价量族(估值/财务/成长/规模依赖坏管线、情绪/资金面数据源未审计 → 均排除,spec §3);
- t 日按因子截面排序,top/bottom quintile 等权的 t→t+1 收益差 = 当日 L/S;族内成员等权平均;
- 下游 regime 特征在 t 只允许用 available_date ≤ t 的行(PIT 命门);
- 全量物化重(10-30min)→ 只走 __main__ 独立子进程 + 独立锁,不进 regen 锁临界区(评审前置条件);
  regen 内只跑日频增量(秒-分钟级)。
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.paths import FACTOR_LS_PARQUET

WHITELIST_FAMILIES = ("动量反转", "技术", "波动率", "流动性", "共振", "跟随")
CSV_ID = "_csv"          # 市场截面收益离散度(连续机会空间代理,深研 3-0)伪因子行
CSV_FAMILY = "_market"   # 不参与族聚合
LS_Q = 0.2
LS_MIN_N = 30            # 截面最少票数,低于不算(诚实缺席)


def ls_series(fac_wide: pd.DataFrame, close_wide: pd.DataFrame, q: float = LS_Q,
              min_n: Optional[int] = None) -> pd.DataFrame:
    """单因子 L/S 日收益(值已预定向:高=看多)。行 index=t、available_date=t+1(PIT)。"""
    mn = LS_MIN_N if min_n is None else int(min_n)
    cw = close_wide.sort_index()
    fw = fac_wide.reindex(index=cw.index, columns=cw.columns)
    ret_next = cw.shift(-1) / cw - 1.0        # r_{t→t+1} 挂在 t 行
    dates = list(cw.index)
    rows = []
    for i, t in enumerate(dates[:-1]):        # 末日无次日收益 → 不出行
        f = fw.loc[t].dropna()
        r = ret_next.loc[t].reindex(f.index).dropna()
        f = f.reindex(r.index)
        if len(f) < mn:
            continue
        n_side = max(1, int(len(f) * q))
        order = f.sort_values()
        top = float(r.reindex(order.index[-n_side:]).mean())
        bot = float(r.reindex(order.index[:n_side]).mean())
        if not (np.isfinite(top) and np.isfinite(bot)):
            continue
        rows.append({"date": t, "ls_ret": top - bot, "available_date": dates[i + 1]})
    return pd.DataFrame(rows, columns=["date", "ls_ret", "available_date"])


def load_family_ls() -> pd.DataFrame:
    """族等权 L/S 长表(date, family, ls_ret, available_date);缺产物 → 空表(诚实缺席)。"""
    if not FACTOR_LS_PARQUET.exists():
        return pd.DataFrame(columns=["date", "family", "ls_ret", "available_date"])
    df = pd.read_parquet(FACTOR_LS_PARQUET)
    df = df[df["family"] != CSV_FAMILY]
    g = (df.groupby(["date", "family"], as_index=False)
           .agg(ls_ret=("ls_ret", "mean"), available_date=("available_date", "max")))
    return g.sort_values(["family", "date"]).reset_index(drop=True)


def load_csv_series() -> pd.Series:
    """市场截面收益离散度序列(index=date);缺产物 → 空序列。"""
    if not FACTOR_LS_PARQUET.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(FACTOR_LS_PARQUET)
    df = df[df["factor_id"] == CSV_ID]
    return pd.Series(df["ls_ret"].values, index=pd.DatetimeIndex(df["date"])).sort_index()


def materialize_factor_frames(universe: str = "csi800", start: str = "2016-01-01",
                              end: Optional[str] = None
                              ) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, Dict[str, str]]:
    """引擎面板 → 白名单因子 wide 值框(已预定向 ×dir)+ close wide(骨架照 factor_ic.py,
    与激活闸共用同一物化)。返回 (frames{fid:df(date×code)}, close_wide, fams{fid:family})。
    编译不过/全 NaN → 诚实跳过并打日志。重函数:只在子进程/闸里调,不进 regen 锁。"""
    from datetime import date as _date

    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.data.universe import resolve_universe_codes
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached

    from guanlan_v2.screen.catalog import FACTOR_DEFS

    end_s = end or _date.today().isoformat()
    codes = [str(c) for c in resolve_universe_codes(universe)]
    panel = load_panel_cached(get_default_loader(), codes, start, end_s, freq="day")
    try:
        from guanlan_v2.workflow.api import _inject_market_refs
        panel, _w = _inject_market_refs(panel, "csi300", None, start, end_s, freq="day")
        for _m in (_w or []):
            print(f"[factor_ls] 警告: {_m}", flush=True)   # 指数停更 → 共振/跟随族缺数显形
    except Exception:  # noqa: BLE001
        pass    # 注入失败 → 共振/跟随族算不出,诚实缺席,其余族不受影响

    def _wide(expr: str):
        s = compile_factor(expr)(panel)
        if s is None or not isinstance(s, pd.Series):
            return None
        w = s.unstack(level="code")
        w.index = pd.DatetimeIndex(w.index)
        return w.sort_index()

    close_wide = _wide("close")
    if close_wide is None or close_wide.empty:
        raise RuntimeError("factor_ls: 面板无 close")
    frames: Dict[str, pd.DataFrame] = {}
    fams: Dict[str, str] = {}
    for fid, meta in FACTOR_DEFS.items():
        fam, expr = meta.get("family"), meta.get("expr")
        if fam not in WHITELIST_FAMILIES or not expr:
            continue
        try:
            w = _wide(expr)
        except Exception:  # noqa: BLE001
            w = None
        if w is None or w.dropna(how="all").empty:
            print(f"[factor_ls] 跳过 {fid}({meta.get('short')}):算不出(诚实缺席)", flush=True)
            continue
        frames[fid] = w * float(meta.get("dir", 1) or 1)   # 预定向(legacy fa_distrib dir=-1)
        fams[fid] = fam
    return frames, close_wide, fams


def _csv_rows(close_wide: pd.DataFrame) -> pd.DataFrame:
    """市场截面收益离散度:当日截面 std,收盘即知(available_date=当日,非前瞻)。"""
    ret = close_wide.pct_change(fill_method=None)
    csv = ret.std(axis=1, ddof=0)
    df = pd.DataFrame({"date": csv.index, "ls_ret": csv.values})
    df = df[np.isfinite(df["ls_ret"])].copy()
    df["available_date"] = df["date"]
    df["factor_id"], df["family"] = CSV_ID, CSV_FAMILY
    return df


_COLS = ["date", "family", "factor_id", "ls_ret", "available_date"]


def compute_factor_ls(universe: str = "csi800", start: str = "2016-01-01",
                      end: Optional[str] = None) -> int:
    """全量物化 → factor_ls_returns.parquet(因子行 + _csv 行)。只允许子进程跑。"""
    frames, close_wide, fams = materialize_factor_frames(universe, start, end)
    parts = []
    for fid, fw in frames.items():
        df = ls_series(fw, close_wide)
        if df.empty:
            continue
        df["factor_id"], df["family"] = fid, fams[fid]
        parts.append(df)
    parts.append(_csv_rows(close_wide))
    out = pd.concat(parts, ignore_index=True)[_COLS]
    tmp = str(FACTOR_LS_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(FACTOR_LS_PARQUET))
    return len(out)


def update_factor_ls_incremental(end: Optional[str] = None,
                                 universe: str = "csi800") -> int:
    """日频增量(regen 非阻断步):只补产物末日之后(短窗 470 自然日重物化,分钟级);
    无全量产物 → 0 并提示(不在 regen 锁内偷跑重活)。幂等:同 end 重跑不重复。"""
    from datetime import date as _date, timedelta

    if not FACTOR_LS_PARQUET.exists():
        print("[factor_ls] 无全量产物,先 python -m guanlan_v2.strategy.compute.factor_ls 回填",
              flush=True)
        return 0
    old = pd.read_parquet(FACTOR_LS_PARQUET)
    last = pd.Timestamp(old["date"].max())
    end_s = end or _date.today().isoformat()
    if pd.Timestamp(end_s) <= last:
        return 0
    start = (last - timedelta(days=470)).date().isoformat()   # 目录最长回看 240 交易日热身
    frames, close_wide, fams = materialize_factor_frames(universe, start, end_s)
    parts = []
    for fid, fw in frames.items():
        df = ls_series(fw, close_wide)
        df = df[df["date"] > last]
        if df.empty:
            continue
        df["factor_id"], df["family"] = fid, fams[fid]
        parts.append(df)
    cdf = _csv_rows(close_wide)
    cdf = cdf[cdf["date"] > last]
    if len(cdf):
        parts.append(cdf)
    if not parts:
        return 0
    new = pd.concat(parts, ignore_index=True)[_COLS]
    out = (pd.concat([old, new], ignore_index=True)
             .drop_duplicates(subset=["date", "factor_id"], keep="first"))
    tmp = str(FACTOR_LS_PARQUET) + ".tmp"
    out.to_parquet(tmp, index=False)
    os.replace(tmp, str(FACTOR_LS_PARQUET))
    return len(new)


def _acquire_ls_lock():
    """独立锁(**非 regen 锁**,评审前置条件):防两个全量回填并发;>2h 残留可接管。"""
    import json
    import tempfile
    import time
    from pathlib import Path

    p = Path(tempfile.gettempdir()) / "guanlan_factor_ls.lock"
    if p.exists():
        try:
            age = time.time() - float(json.loads(p.read_text(encoding="utf-8")).get("ts", 0))
        except Exception:  # noqa: BLE001
            age = 1e9
        if age < 7200:
            raise RuntimeError("另一 factor_ls 全量回填进行中,拒绝并发")
    p.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")
    return p


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="因子族 L/S 全量回填(独立子进程,10-30min)")
    ap.add_argument("--universe", default="csi800")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default=None)
    a = ap.parse_args()
    _lock = _acquire_ls_lock()
    try:
        n = compute_factor_ls(a.universe, a.start, a.end)
        print(f"factor_ls 全量回填 {n} 行 -> {FACTOR_LS_PARQUET}", flush=True)
    finally:
        try:
            _lock.unlink()
        except Exception:  # noqa: BLE001
            pass
