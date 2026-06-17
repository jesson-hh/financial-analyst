# -*- coding: utf-8 -*-
"""引擎原生「主线雷达」板块面板 + 月级状态 —— 把 qlib 版迁到引擎二进制读取。

**不依赖 qlib 包**,逐位对齐三支 qlib 脚本:

- ``src/data/industry_mapping.py``                  → :func:`load_stock_industry_map`(常量+逻辑照搬)
- ``strategy/mainline/build_sector_panel.py``       → :func:`build_sector_panel_industry`
- ``strategy/mainline/compute_monthly_mainlines.py``→ :func:`compute_indicators` + :func:`classify_status`

产物 ``monthly_mainlines_panel.parquet`` 只由 **industry 面板** 派生(qlib 的 cluster
面板不参与),故本模块只建 industry 面板,省一半工。

保真要点(同 breadth 的两课):
* ``ret = close.ffill().pct_change()``,且 ``close`` 走 **float32**(复刻 qlib ``$close``
  在涨停阈值上的舍入:主板 9.5% / 双创 SH688·SZ30 19.5% / 北交 BJ 29.5%);
* ``total_mv`` 也走 **float32**(mv_yi 的市值分档边界 50/100/200/500 亿是精确阈值,
  float64/float32 会落两侧);amount/turnover_rate 保 float64(只进求和/均值,分位对噪声鲁棒)。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.paths import STOCK_BASIC_PARQUET
from guanlan_v2.strategy.compute.units import normalize_frame_units

# ── 行业→8 簇映射常量(照搬 src/data/industry_mapping.py)──────────────────────
CLUSTER_NAMES = {
    0: "金融地产", 1: "消费", 2: "医药", 3: "TMT",
    4: "上游周期", 5: "中游制造", 6: "公用基建", 7: "综合",
}
INDUSTRY_TO_CLUSTER = {
    "银行": 0, "证券": 0, "多元金融": 0, "保险": 0, "区域地产": 0, "全国地产": 0,
    "房产服务": 0, "园区开发": 0,
    "食品": 1, "白酒": 1, "乳制品": 1, "软饮料": 1, "红黄酒": 1, "啤酒": 1,
    "酒店餐饮": 1, "家用电器": 1, "家居用品": 1, "电器连锁": 1, "服饰": 1, "纺织": 1,
    "百货": 1, "超市连锁": 1, "商品城": 1, "批发业": 1, "商贸代理": 1, "其他商业": 1,
    "旅游景点": 1, "旅游服务": 1, "文教休闲": 1, "日用化工": 1, "农业综合": 1,
    "种植业": 1, "饲料": 1, "渔业": 1, "林业": 1, "广告包装": 1,
    "医疗保健": 2, "化学制药": 2, "生物制药": 2, "中成药": 2, "医药商业": 2,
    "元器件": 3, "软件服务": 3, "半导体": 3, "通信设备": 3, "互联网": 3, "IT设备": 3,
    "电信运营": 3, "影视音像": 3, "出版业": 3,
    "化工原料": 4, "塑料": 4, "化纤": 4, "染料涂料": 4, "橡胶": 4, "农药化肥": 4,
    "焦炭加工": 4, "石油开采": 4, "石油加工": 4, "石油贸易": 4, "煤炭开采": 4,
    "小金属": 4, "铝": 4, "铜": 4, "铅锌": 4, "黄金": 4, "普钢": 4, "特种钢": 4,
    "钢加工": 4, "其他建材": 4, "水泥": 4, "玻璃": 4, "矿物制品": 4, "陶瓷": 4, "造纸": 4,
    "电气设备": 5, "专用机械": 5, "汽车配件": 5, "机械基件": 5, "建筑工程": 5,
    "电器仪表": 5, "工程机械": 5, "运输设备": 5, "汽车整车": 5, "机床制造": 5,
    "新型电力": 5, "汽车服务": 5, "装修装饰": 5, "化工机械": 5, "农用机械": 5,
    "摩托车": 5, "纺织机械": 5, "船舶": 5, "轻工机械": 5,
    "供气供热": 6, "环境保护": 6, "水务": 6, "火力发电": 6, "水力发电": 6,
    "仓储物流": 6, "航空": 6, "水运": 6, "空运": 6, "港口": 6, "路桥": 6, "公路": 6,
    "铁路": 6, "公共交通": 6, "机场": 6,
    "综合类": 7,
}

FETCH_START_DEFAULT = "2023-08-01"


def _ts_to_qlib(ts_code: str) -> str:
    num, mkt = ts_code.split(".")
    return f"{mkt}{num}"


def load_stock_industry_map(stock_basic_path: Optional[Path] = None) -> pd.DataFrame:
    """读 vendored ``tushare_stock_basic.parquet`` → [instrument, name, industry,
    cluster_id, cluster_name](照搬 qlib src/data/industry_mapping.load_stock_industry_map)。"""
    p = Path(stock_basic_path) if stock_basic_path else STOCK_BASIC_PARQUET
    df = pd.read_parquet(p)[["ts_code", "name", "industry"]].copy()
    df["instrument"] = df["ts_code"].map(_ts_to_qlib)
    df["cluster_id"] = df["industry"].map(INDUSTRY_TO_CLUSTER)
    df["cluster_name"] = df["cluster_id"].map(CLUSTER_NAMES)
    df = df.dropna(subset=["cluster_id"]).copy()
    df["cluster_id"] = df["cluster_id"].astype(int)
    return df[["instrument", "name", "industry", "cluster_id", "cluster_name"]]


# ── 1) industry 板块面板(对齐 build_sector_panel.py 的 industry 分支)──────────
def build_sector_panel_industry(
    loader,
    codes: List[str],
    imap: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    frames: List[pd.DataFrame] = []
    for code in codes:
        c = loader._read_bin(code, "close")
        a = loader._read_bin(code, "amount")
        t = loader._read_bin(code, "turnover_rate")
        m = loader._read_bin(code, "total_mv")
        if c is None or a is None:
            continue
        df = pd.DataFrame({"close": c, "amount": a})
        df["turnover_rate"] = t if t is not None else np.nan
        df["total_mv"] = m if m is not None else np.nan
        df = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        if df.empty:
            continue
        # 量纲校准(2026-06-12):amount=千元 批次 → ×1000(volume 仅作参照定标,
        # 不入面板;见 compute/units.py)。否则 total_amount_yi/amt_rank 失真。
        df = normalize_frame_units(df, ref_vol=loader._read_bin(code, "volume"))
        # 同 breadth:ffill 复牌 + float32 复刻 qlib 涨停阈值舍入
        df["ret"] = df["close"].astype("float32").ffill().pct_change(fill_method=None)
        df["instrument"] = code
        frames.append(df)

    if not frames:
        raise RuntimeError("build_sector_panel_industry: 无可读股票")

    mkt = pd.concat(frames, axis=0)
    mkt.index.name = "datetime"
    mkt = mkt.reset_index()
    # mv_yi:total_mv(万元)→亿元;total_mv 走 float32 复刻 qlib $total_mv 分档边界舍入
    mkt["mv_yi"] = mkt["total_mv"].astype("float32") / 1e4
    # turnover_rate 也走 float32 复刻 qlib $turnover_rate:否则 mean_turnover 的 float64
    # 微差会翻转其滚动分位(mean_turnover_pct_*d)的个别名次。
    mkt["turnover_rate"] = mkt["turnover_rate"].astype("float32")

    mkt = mkt.dropna(subset=["ret"])
    mkt = mkt[mkt["amount"] > 0]

    # 板块涨停阈值:主板 9.5% / SH688·SZ30 19.5% / BJ 29.5%
    code_str = mkt["instrument"].astype(str)
    is_kc = code_str.str.startswith("SH688")
    is_cyb = code_str.str.startswith("SZ30")
    is_bj = code_str.str.startswith("BJ")
    threshold = pd.Series(0.095, index=mkt.index)
    threshold[is_kc | is_cyb] = 0.195
    threshold[is_bj] = 0.295
    mkt["is_lu"] = mkt["ret"] >= threshold

    # join 行业
    mkt = mkt.merge(imap[["instrument", "industry", "cluster_name"]], on="instrument", how="left")
    mkt = mkt.dropna(subset=["industry"])

    # 主聚合
    grp = mkt.groupby(["datetime", "industry"])
    panel = grp.agg(
        lu_count=("is_lu", "sum"),
        up_count=("ret", lambda x: (x > 0).sum()),
        down_count=("ret", lambda x: (x < 0).sum()),
        stock_count=("ret", "count"),
        mean_ret=("ret", "mean"),
        total_amount=("amount", "sum"),
        mean_turnover=("turnover_rate", "mean"),
        mv_top3_avg=("mv_yi", lambda x: float(x.nlargest(3).mean()) if len(x) >= 1 else 0.0),
    ).reset_index()

    # 大龙:涨停票里 mv_yi 最大(无涨停=0)—— 用 lu-only groupby 后并回
    lu_only = mkt[mkt["is_lu"]].copy()
    lu_mvmax = (lu_only.groupby(["datetime", "industry"])["mv_yi"].max()
                .rename("lu_max_mv_yi").reset_index())
    panel = panel.merge(lu_mvmax, on=["datetime", "industry"], how="left")
    # 复刻 qlib:无涨停(lu_count==0)→ 0.0;有涨停但其 mv 全 NaN → max=NaN(保留不填 0,
    # 让 NaN 经 60 日滚动均值传播,从而正确否决 mainline 判定 lu_max_60>=200)。
    panel.loc[panel["lu_count"] == 0, "lu_max_mv_yi"] = 0.0

    panel["up_ratio"] = panel["up_count"] / panel["stock_count"]
    panel["lu_ratio"] = panel["lu_count"] / panel["stock_count"]
    panel["total_amount_yi"] = panel["total_amount"] / 1e8

    # 涨停票市值分档
    lu_only["mv_tier"] = pd.cut(
        lu_only["mv_yi"],
        bins=[-1, 50, 100, 200, 500, 1e10],
        labels=["lu_mv_lt50", "lu_mv_50_100", "lu_mv_100_200", "lu_mv_200_500", "lu_mv_ge500"],
    )
    tier_counts = (lu_only.groupby(["datetime", "industry", "mv_tier"], observed=True)
                   .size().unstack(fill_value=0).reset_index())
    panel = panel.merge(tier_counts, on=["datetime", "industry"], how="left")
    for c in ["lu_mv_lt50", "lu_mv_50_100", "lu_mv_100_200", "lu_mv_200_500", "lu_mv_ge500"]:
        if c not in panel.columns:
            panel[c] = 0
        panel[c] = panel[c].fillna(0)
    panel["lu_count_ge200_yi"] = panel.get("lu_mv_200_500", 0) + panel.get("lu_mv_ge500", 0)
    panel["lu_count_ge500_yi"] = panel.get("lu_mv_ge500", 0)

    # 滚动 60/20 日分位(按 industry)
    panel = panel.sort_values(["industry", "datetime"])
    for col in ["lu_count", "mean_ret", "total_amount", "up_ratio", "lu_ratio", "mean_turnover"]:
        panel[f"{col}_pct_60d"] = panel.groupby("industry")[col].transform(
            lambda x: x.rolling(60, min_periods=20).rank(pct=True))
        panel[f"{col}_pct_20d"] = panel.groupby("industry")[col].transform(
            lambda x: x.rolling(20, min_periods=10).rank(pct=True))

    # 当日跨行业排名
    panel["lu_rank_today"] = panel.groupby("datetime")["lu_count"].rank(method="min", ascending=False)
    panel["ret_rank_today"] = panel.groupby("datetime")["mean_ret"].rank(method="min", ascending=False)
    panel["amt_rank_today"] = panel.groupby("datetime")["total_amount"].rank(method="min", ascending=False)

    return panel


# ── 2) 月级指标(照搬 compute_monthly_mainlines.compute_indicators)─────────────
def compute_indicators(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["datetime"] = pd.to_datetime(panel["datetime"])
    panel = panel.sort_values(["industry", "datetime"])

    for n in [10, 20, 60]:
        panel[f"mean_ret_{n}d_sec"] = panel.groupby("industry")["mean_ret"].rolling(n).sum().reset_index(level=0, drop=True)

    baseline = panel.groupby("datetime").agg(
        base_mean_ret=("mean_ret", "mean"),
        base_lu_count_avg=("lu_count", "mean"),
    ).sort_index()
    for n in [10, 20, 60]:
        baseline[f"base_{n}d_sum"] = baseline["base_mean_ret"].rolling(n).sum()
    baseline = baseline.reset_index()
    panel = panel.merge(
        baseline[["datetime", "base_10d_sum", "base_20d_sum", "base_60d_sum"]],
        on="datetime", how="left",
    )
    for n in [10, 20, 60]:
        panel[f"ex_{n}d"] = (panel[f"mean_ret_{n}d_sec"] - panel[f"base_{n}d_sum"]) * 100

    panel["is_top10"] = (panel["lu_rank_today"] <= 10).astype(int)
    for n in [20, 60]:
        panel[f"top10_ratio_{n}d"] = panel.groupby("industry")["is_top10"].rolling(n).mean().reset_index(level=0, drop=True)

    for n in [20, 60]:
        panel[f"lu_max_mv_{n}d_mean"] = panel.groupby("industry")["lu_max_mv_yi"].rolling(n).mean().reset_index(level=0, drop=True)
        panel[f"lu_count_{n}d_sum"] = panel.groupby("industry")["lu_count"].rolling(n).sum().reset_index(level=0, drop=True)

    return panel


def classify_status(row) -> str:
    """照搬 compute_monthly_mainlines.classify_status(5 状态规则版)。"""
    ex60 = row.get("ex_60d", 0) or 0
    ex20 = row.get("ex_20d", 0) or 0
    ex10 = row.get("ex_10d", 0) or 0
    top10_60 = row.get("top10_ratio_60d", 0) or 0
    top10_20 = row.get("top10_ratio_20d", 0) or 0
    lu_max_60 = row.get("lu_max_mv_60d_mean", 0) or 0
    lu_cnt_20 = row.get("lu_count_20d_sum", 0) or 0

    if ex60 > 0 and top10_60 > 0.20 and ex10 < -2:
        return "decay"
    if ex60 >= 5 and top10_60 >= 0.30 and lu_max_60 >= 200:
        return "mainline"
    if ex60 > 0 and ex20 <= -10 and top10_60 >= 0.20:
        return "revival"
    if ex60 < 5 and lu_cnt_20 >= 5 and top10_20 >= 0.20:
        return "initiation"
    if ex60 <= 0 and top10_60 < 0.10:
        return "cold"
    return "neutral"


# ── 3) 编排:一次出 monthly_mainlines 面板(含 status)────────────────────────
def build_mainline(
    provider_uri: str,
    start: str = FETCH_START_DEFAULT,
    end: str = "2026-06-05",
    codes: Optional[List[str]] = None,
    stock_basic_path: Optional[Path] = None,
) -> pd.DataFrame:
    """引擎原生构建月级主线面板(== qlib monthly_mainlines_panel.parquet)。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from guanlan_v2.strategy.compute.breadth import list_all_instruments

    loader = QlibBinaryLoader(provider_uri)
    if codes is None:
        codes = list_all_instruments(provider_uri)
    imap = load_stock_industry_map(stock_basic_path)
    panel = build_sector_panel_industry(loader, codes, imap, start, end)
    panel = compute_indicators(panel)
    panel["status"] = panel.apply(classify_status, axis=1)
    return panel
