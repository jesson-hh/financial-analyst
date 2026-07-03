# -*- coding: utf-8 -*-
"""因子 vintage IC:逐日截面 rank-IC 序列的 as-of(PIT 真 OOS)查表。

vintage as-of D = 只用 D 当天已知(realized_date≤D)的逐日 IC、取最近 window 条求均值;
样本不足 → None(诚实降级,不编数)。批算落盘见 compute_factor_vintage(后续任务)。
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

CS_IC_PARQUET = Path(__file__).resolve().parents[2] / "var" / "factor_vintage_cs_ic.parquet"
TSIC_PARQUET = Path(__file__).resolve().parents[2] / "var" / "factor_vintage_tsic.parquet"

# tsic 单票口径范围(落子固定盘;后续任务用)
SEATS_POOL_CODES = ["SZ300750", "SH600519", "SZ002594", "SZ300308",
                    "SH601012", "SH600036", "SH605358"]


def _realized_map(uniq_dates, horizon: int) -> dict:
    """逐日 → 其 fwd 实现日(date 列表的 +horizon 位);尾部 horizon 天无实现日则不入。"""
    ds = [str(d)[:10] for d in uniq_dates]
    out = {}
    for i, d in enumerate(ds):
        if i + horizon < len(ds):
            out[d] = ds[i + horizon]
    return out


def _sweep_items():
    """vintage 扫描面 = 选股目录 FACTOR_DEFS + factorlib 待审 draft(P4:度量不上架——
    draft 仍不进选股目录,但前向真实表现从出生起就有档可查)。"""
    from guanlan_v2.screen.catalog import FACTOR_DEFS
    items = list(FACTOR_DEFS.items())
    have = {str(k) for k, _ in items}
    try:
        from guanlan_v2.factorlib.store import LibraryFactorStore
        for f in LibraryFactorStore().list_factors(validate=False):
            nm, expr = str(f.get("name") or ""), f.get("expr")
            if f.get("status") == "draft" and expr and nm and nm not in have:
                items.append((nm, {"expr": expr, "family": "draft"}))
    except Exception:  # noqa: BLE001 — draft 并入失败不挡正式因子 vintage
        pass
    return items


def compute_factor_vintage(universe: str = "csi300", years: float = 2.0, horizon: int = 5,
                           end: Optional[str] = None, pool_codes=None) -> dict:
    """全 catalog(+ factorlib draft)逐日截面 vintage IC + pool×factorlib tsic → 两 parquet。
    返回 {cs_rows, tsic_rows}。一次面板加载 + 一遍因子编译同产两表。"""
    import pandas as pd
    from datetime import date, timedelta
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.data.universe import resolve_universe_codes
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    from guanlan_v2.screen.catalog import FACTOR_DEFS

    pool = [str(c) for c in (pool_codes or SEATS_POOL_CODES)]
    end_d = date.fromisoformat(end) if end else date.today()
    start = (end_d - timedelta(days=int(365 * years) + 260)).isoformat()  # +260 热身
    end_s = end_d.isoformat()

    codes = sorted(set([str(c) for c in resolve_universe_codes(universe)] + pool))  # 并入 pool 保证有本票列
    loader = get_default_loader()
    panel = load_panel_cached(loader, codes, start, end_s, freq="day")
    try:
        from guanlan_v2.workflow.api import _inject_market_refs
        panel, _w = _inject_market_refs(panel, "csi300", None, start, end_s, freq="day")
    except Exception:  # noqa: BLE001
        pass

    close = compile_factor("close")(panel)
    fwd = close.groupby(level="code").shift(-horizon) / close - 1.0

    # tsic 限 factorlib 因子:catalog.py 把入库因子标 family="因子库"、id 以 lib_ 起头(无 source 字段)。
    # 宁宽不漏:family 含"库" 或 id 以 lib_ 起头 → 计入 tsic(目的=只算入库因子,别算满 catalog)。
    fl_ids = {fid for fid, m in FACTOR_DEFS.items()
              if "库" in str(m.get("family", "")) or str(fid).startswith("lib_")}

    cs_rows, tsic_rows = [], []
    for fid, meta in _sweep_items():
        expr = meta.get("expr")
        if not expr:
            continue
        try:
            fac = compile_factor(expr)(panel)
            if fac is None or not isinstance(fac, pd.Series):
                continue
            d = pd.DataFrame({"f": fac, "r": fwd, "c": fac.index.get_level_values("code"),
                              "t": fac.index.get_level_values("datetime")}).dropna(subset=["f", "r"])
            if d.empty:
                continue
            uniq = sorted(pd.Index(d["t"]).unique())
            rmap = _realized_map([str(x)[:10] for x in uniq], horizon)
            _dir = float(meta.get("dir", 1) or 1)
            for t in uniq:
                ts = str(t)[:10]
                if ts not in rmap:        # 尾部未实现 → 跳(诚实:无 realized_date 不落)
                    continue
                sub = d[d["t"] == t]
                if len(sub) >= 30:
                    ic_t = sub["f"].rank().corr(sub["r"].rank())
                    if pd.notna(ic_t):
                        cs_rows.append({"id": fid, "date": ts, "ic": round(_dir * float(ic_t), 4),
                                        "n": int(len(sub)), "realized_date": rmap[ts]})
            if fid in fl_ids:   # tsic:仅 factorlib 因子 × pool 票
                for code in pool:
                    sc = d[d["c"].astype(str) == code]
                    for _, row in sc.iterrows():
                        ts = str(row["t"])[:10]
                        if ts in rmap:
                            tsic_rows.append({"code": code, "id": fid, "date": ts,
                                              "fval": float(row["f"]), "fwd": float(row["r"]),
                                              "realized_date": rmap[ts]})
        except Exception:  # noqa: BLE001
            continue

    for rows, p in ((cs_rows, CS_IC_PARQUET), (tsic_rows, TSIC_PARQUET)):
        out = pd.DataFrame(rows)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(p) + ".tmp"
        out.to_parquet(tmp, index=False)
        os.replace(tmp, str(p))
    return {"cs_rows": len(cs_rows), "tsic_rows": len(tsic_rows)}


def cs_vintage_from_frame(df, factor_id: str, date: str, window: int = 60,
                          horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    """截面 vintage IC as-of(纯函数)。df 列 [id,date,ic,n,realized_date]。
    只取 realized_date≤date 的真 OOS 行,date 最近 window 条求均值;<min_n → None。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    sub = df[df["id"].astype(str) == str(factor_id)].copy()
    if sub.empty:
        return None
    sub = sub[sub["realized_date"].astype(str) <= str(date)]      # OOS 闸门:绝不取 >D
    if sub.empty:
        return None
    sub = sub.sort_values("date").tail(int(window))               # trailing 窗
    if len(sub) < int(min_n):
        return None
    ics = sub["ic"].astype(float)
    m = float(ics.mean())
    return {"ic": round(m, 4), "n": int(len(sub)), "dir": (1 if m >= 0 else -1),
            "asof": str(sub["date"].iloc[-1])}


_cs_cache = {"mtime": None, "df": None}


def load_cs_vintage():
    """读 cs vintage 表 → DataFrame;缺文件 → None。mtime 缓存。"""
    import pandas as pd
    p = CS_IC_PARQUET
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    if _cs_cache["mtime"] != mt:
        try:
            _cs_cache["df"] = pd.read_parquet(p)
            _cs_cache["mtime"] = mt
        except Exception:  # noqa: BLE001
            return None
    return _cs_cache["df"]


def cs_vintage_asof(factor_id: str, date: str, window: int = 60,
                    horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    return cs_vintage_from_frame(load_cs_vintage(), factor_id, date, window, horizon, min_n)


def tsic_vintage_from_frame(df, code: str, factor_id: str, date: str, window: int = 60,
                            horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    """单票 tsic vintage as-of(纯函数)。df 列 [code,id,date,fval,fwd,realized_date]。
    取本票本因子 realized_date≤date 的 trailing window 行,Spearman(fval,fwd);<min_n → None。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    sub = df[(df["code"].astype(str) == str(code)) & (df["id"].astype(str) == str(factor_id))].copy()
    if sub.empty:
        return None
    sub = sub[sub["realized_date"].astype(str) <= str(date)].sort_values("date").tail(int(window))
    sub = sub.dropna(subset=["fval", "fwd"])
    if len(sub) < int(min_n):
        return None
    ic = sub["fval"].rank().corr(sub["fwd"].rank())   # Spearman
    if pd.isna(ic):
        return None
    return {"ic": round(float(ic), 4), "n": int(len(sub)),
            "dir": (1 if ic >= 0 else -1), "asof": str(sub["date"].iloc[-1])}


_tsic_cache = {"mtime": None, "df": None}


def load_tsic_vintage():
    """读 tsic vintage 表 → DataFrame;缺文件 → None。mtime 缓存。"""
    import pandas as pd
    p = TSIC_PARQUET
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    if _tsic_cache["mtime"] != mt:
        try:
            _tsic_cache["df"] = pd.read_parquet(p)
            _tsic_cache["mtime"] = mt
        except Exception:  # noqa: BLE001
            return None
    return _tsic_cache["df"]


def tsic_vintage_asof(code: str, factor_id: str, date: str, window: int = 60,
                      horizon: int = 5, min_n: int = 10) -> Optional[dict]:
    return tsic_vintage_from_frame(load_tsic_vintage(), code, factor_id, date, window, horizon, min_n)


def factor_z_from_frame(df, code: str, factor_id: str, date: str, window: int = 60,
                        min_n: int = 10) -> Optional[dict]:
    """单票本因子 fval 的 trailing z 分(纯函数)。df 列含 [code,id,date,fval]。
    取 date≤date 的最近 window 条 fval,z=(当前fval−mean)/std;<min_n 或 std=0 → None。
    fval 在其 date 当日已知(PIT 安全),无需 realized_date 闸门。"""
    import pandas as pd
    if df is None or len(df) == 0:
        return None
    sub = df[(df["code"].astype(str) == str(code)) & (df["id"].astype(str) == str(factor_id))].copy()
    if sub.empty:
        return None
    sub = sub[sub["date"].astype(str) <= str(date)].sort_values("date").tail(int(window))
    sub = sub.dropna(subset=["fval"])
    if len(sub) < int(min_n):
        return None
    vals = sub["fval"].astype(float)
    sd = float(vals.std())
    if not (sd > 0):
        return None
    cur = float(vals.iloc[-1])
    return {"z": round((cur - float(vals.mean())) / sd, 4), "fval": cur,
            "n": int(len(sub)), "asof": str(sub["date"].iloc[-1])}


def factor_z_asof(code: str, factor_id: str, date: str, window: int = 60,
                  min_n: int = 10) -> Optional[dict]:
    return factor_z_from_frame(load_tsic_vintage(), code, factor_id, date, window, min_n)
