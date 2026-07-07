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


def _ensure_v4_columns(df):
    """变体排名补齐 V4_COLUMNS:工作流模型只产 code/date/lgb_pct。
    缺 v4_total/v4_layer → NaN/None(→ /screen 自然走 lgb_pct-only 分支,诚实按模型分位选股,不冒充五维评级);
    缺 lgb_rank → 按 lgb_pct 降序派生;缺 lgb_score → NaN。已有列不动(v4 变体本就齐全 → no-op)。"""
    import pandas as pd
    if "lgb_pct" not in df.columns:
        return df  # 结构异常 → 原样交给消费方/上游校验
    out = df.copy()
    if "lgb_rank" not in out.columns:
        out["lgb_rank"] = out["lgb_pct"].rank(ascending=False, method="first").astype(int)
    if "lgb_score" not in out.columns:
        out["lgb_score"] = float("nan")
    if "v4_total" not in out.columns:
        out["v4_total"] = float("nan")
    if "v4_layer" not in out.columns:
        out["v4_layer"] = None
    return out


def load_v4_ranking(model_id=None):
    """读 v4 排名;model_id 缺省/"prod" → 生产老路径(vendored 全市场);否则
    models/<id>/v4_ranking.parquet。缺文件 → FileNotFoundError(诚实,不造数据)。"""
    import pandas as pd

    if model_id and model_id != "prod":
        from guanlan_v2.screen.model_registry import variant_ranking_path

        p = variant_ranking_path(model_id)
        if not p.exists():
            raise FileNotFoundError(f"v4 变体产物缺失: {p}")
        return _ensure_v4_columns(pd.read_parquet(p))
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


def v4_pct_map(df=None, model_id=None) -> Dict[str, float]:
    """v4 榜 → ``{code: pct(0-100)}`` 的唯一列名/量纲归一入口。
    列兼容:``code`` | ``ts_code``;``lgb_pct``(生产 rank(pct=True) 值域 0-1)×100、
    ``pct``(0-100)原样。缺列 → ValueError(诚实,不猜)。
    此口径此前 rescore.v4_pool 与 industry.aggregate._v4_pct_map 各手写一份、量纲判据还不一致
    (前者按值 v≤1、后者按列名 lgb_pct),2026-07-04 v4 换列名 lgb_pct 时 rescore 整体拒开跑
    已咬过一次 → 收拢至此,漂移只修一处。df 省略则 load_v4_ranking(model_id)。"""
    if df is None:
        df = load_v4_ranking(model_id=model_id)
    codecol = "code" if "code" in df.columns else ("ts_code" if "ts_code" in df.columns else None)
    pctcol = "lgb_pct" if "lgb_pct" in df.columns else ("pct" if "pct" in df.columns else None)
    if not codecol or not pctcol:
        raise ValueError(f"v4 榜列缺失(code/lgb_pct): {list(df.columns)}")
    vals = df[pctcol].astype(float)
    if pctcol == "lgb_pct":   # 0-1 分位 → 0-100(下游 _stock_rows/rescore 均按 0-100 消费)
        vals = vals * 100.0
    return dict(zip(df[codecol].astype(str), vals))


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
