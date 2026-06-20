# -*- coding: utf-8 -*-
"""消费 vendored v4 排名产物(Option 1:计算留外部,本仓消费)。

提供:
- ``load_v4_ranking()`` → DataFrame   全市场 v4 排名(lgb_rank/lgb_pct + 顶200 v4_total/v4_layer)
- ``ranking_date()``    → str          产物内排名日期(YYYY-MM-DD)
- ``name_industry_map()`` → dict       qlib_code → (name, industry),来自 tushare_stock_basic
- ``ts_to_qlib(ts_code)`` → str        '600519.SH' → 'SH600519'

不依赖 qlib/引擎;只读 vendored parquet(见 ``_PROVENANCE.md``)。产物刷新 = 在有 qlib 的
环境重跑 v4 → 覆盖 vendor/artifacts/v4_ranking_latest.parquet → 重生 _provenance.json。
"""
from __future__ import annotations

import functools
from typing import Dict, Tuple

from guanlan_v2.strategy.paths import ARTIFACTS_DIR, STOCK_BASIC_PARQUET, V4_RANKING_PARQUET

MAINLINE_PARQUET = ARTIFACTS_DIR / "monthly_mainlines_panel.parquet"

# v4 排名产物列契约(变了 → 契约测试 test_strategy_ranking 红)
V4_COLUMNS = ("code", "lgb_score", "lgb_pct", "lgb_rank", "v4_total", "v4_layer", "date")


def ts_to_qlib(ts_code: str) -> str:
    """tushare ts_code('600519.SH')→ qlib code('SH600519');无点原样返回。"""
    ts = str(ts_code or "").strip()
    if "." in ts:
        num, mkt = ts.split(".", 1)
        return f"{mkt}{num}"
    return ts


def load_v4_ranking(model_id=None):
    """读 v4 排名;model_id 缺省/"prod" → 生产老路径(vendored 全市场);否则
    models/<id>/v4_ranking.parquet。缺文件 → FileNotFoundError(诚实,不造数据)。"""
    import pandas as pd

    if model_id and model_id != "prod":
        from guanlan_v2.screen.model_registry import variant_ranking_path

        p = variant_ranking_path(model_id)
        if not p.exists():
            raise FileNotFoundError(f"v4 变体产物缺失: {p}")
        return pd.read_parquet(p)
    if not V4_RANKING_PARQUET.exists():
        raise FileNotFoundError(
            f"v4 排名产物缺失: {V4_RANKING_PARQUET}(需在有 qlib 环境跑 v4_ranking 刷新)"
        )
    return pd.read_parquet(V4_RANKING_PARQUET)


def ranking_date(model_id=None) -> str:
    """产物内排名日期(YYYY-MM-DD);缺文件/缺列 → 空串。"""
    try:
        df = load_v4_ranking(model_id=model_id)
    except FileNotFoundError:
        return ""
    if "date" in df.columns and len(df):
        return str(df["date"].iloc[0])
    return ""


@functools.lru_cache(maxsize=1)
def name_industry_map() -> Dict[str, Tuple[str, str]]:
    """qlib_code → (name, industry),来自 tushare_stock_basic。缺文件 → 空 dict。"""
    import pandas as pd

    out: Dict[str, Tuple[str, str]] = {}
    if not STOCK_BASIC_PARQUET.exists():
        return out
    b = pd.read_parquet(STOCK_BASIC_PARQUET)
    cols = set(b.columns)
    for r in b.itertuples(index=False):
        ts = getattr(r, "ts_code", None)
        if ts is None:
            continue
        qc = ts_to_qlib(ts)
        nm = str(getattr(r, "name", "") or qc) if "name" in cols else qc
        ind = str(getattr(r, "industry", "") or "") if "industry" in cols else ""
        out[qc] = (nm, ind)
    return out


@functools.lru_cache(maxsize=1)
def mainline_status_map() -> Dict[str, Dict]:
    """行业 → {status, golden, as_of}(L2 主线雷达,读 vendored 月度面板最新截面)。

    status ∈ mainline/revival/initiation/decay/cold/neutral;golden = 上月 initiation →
    本月 mainline(★ 金信号,实证 fwd_60d +5.54pp)。行业名与 tushare_basic 同表(110/110)。
    缺文件 → 空 dict。
    """
    import pandas as pd

    out: Dict[str, Dict] = {}
    if not MAINLINE_PARQUET.exists():
        return out
    mp = pd.read_parquet(MAINLINE_PARQUET, columns=["datetime", "industry", "status"])
    mp["datetime"] = pd.to_datetime(mp["datetime"])
    dts = sorted(mp["datetime"].unique())
    if not dts:
        return out
    last = dts[-1]
    as_of = str(pd.Timestamp(last).date())
    prev_status: Dict[str, str] = {}
    if len(dts) >= 2:
        pv = mp[mp["datetime"] == dts[-2]]
        prev_status = {str(r.industry): str(r.status) for r in pv.itertuples(index=False)}
    for r in mp[mp["datetime"] == last].itertuples(index=False):
        ind, st = str(r.industry), str(r.status)
        golden = (st == "mainline" and prev_status.get(ind) == "initiation")
        out[ind] = {"status": st, "golden": bool(golden), "as_of": as_of}
    return out
