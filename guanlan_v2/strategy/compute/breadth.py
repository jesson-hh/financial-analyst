# -*- coding: utf-8 -*-
"""引擎原生「市场宽度(节奏)」面板/残差计算 —— 把 qlib 版迁到引擎二进制读取。

**不依赖 qlib 包**:直读引擎 ``QlibBinaryLoader`` 的 ``.bin``(py3.13 可跑),
逐位对齐两支 qlib 脚本:

- ``strategy/research/r27_build_market_breadth.py``     → :func:`build_breadth_panel`
- ``tsfm_exp/scripts/ic_probe_market_breadth.py``        → :func:`compute_breadth_resid`

口径与 qlib 版**完全一致**(同读已修复的 ``$amount`` 二进制,故结果应逐位吻合):

* 单股日收益 ``ret = close.pct_change()``(先切 [start,end] 再算,首日 NaN);
* 丢 ``ret`` 为 NaN(首日/停牌断点) + ``amount<=0``(停牌当日);
* 日聚合:total_amount / stock_count / up|down|flat_count /
  limit_up_10(≥9.5% & <19.5%) / limit_up_20(≥19.5%) / limit_down_10(≤-9.5%) /
  mean_ret / median_ret;
* 派生:up_ratio、limit_up_total、total_amount_yi(/1e8);
* 滚动 60/250 日分位(min_periods=20);
* 残差:60 日窗 OLS ``limit_up_total ~ total_amount_yi`` 取当日残差(lu_resid),
  反向回归取 amt_resid,再各自 60 日滚动百分位。

迁移校验:见 ``scripts/compare_breadth.py`` —— 与 qlib 产物逐日对比,差异需在
浮点误差(残差 |Δ|<1e-6,分位 |Δ|<1e-9)内。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from guanlan_v2.strategy.compute.units import normalize_frame_units

# r27 / ic_probe 的固定参数(原样搬运)
ROLL_WINS: Tuple[int, ...] = (60, 250)
RESID_WIN: int = 60
LIMIT_UP_10_LO, LIMIT_UP_10_HI = 0.095, 0.195
LIMIT_UP_20 = 0.195
LIMIT_DOWN_10 = -0.095

PANEL_COLS_RESID_OUT = ["lu_resid", "amt_resid", "lu_resid_pct60", "amt_resid_pct60"]


# ---------------------------------------------------------------------------
# 1) 全市场代码(对齐 qlib D.instruments('csiall') —— 实测 all.txt ≡ csiall.txt)
# ---------------------------------------------------------------------------
def list_all_instruments(provider_uri: str) -> List[str]:
    """读 ``<provider_uri>/instruments/all.txt`` 返回 qlib 形代码(``SH600519``)。

    qlib 版 r27 用 ``D.instruments('csiall')``;经核对 all.txt 与 csiall.txt 完全一致
    (6075 码,零差),故直接读 all.txt 即同一 universe。
    """
    p = Path(provider_uri) / "instruments" / "all.txt"
    out: List[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip():
            out.append(parts[0].strip().upper())
    return out


# ---------------------------------------------------------------------------
# 2) 日度宽度面板(对齐 r27)
# ---------------------------------------------------------------------------
def build_breadth_panel(
    loader,
    codes: List[str],
    start: str,
    end: str,
) -> pd.DataFrame:
    """逐股读 close/amount → 单股 ret → 日聚合宽度面板(datetime 索引)。

    Parameters
    ----------
    loader:
        ``QlibBinaryLoader`` 实例(只用 ``_read_bin`` 直读二进制)。
    codes:
        全市场代码列表(:func:`list_all_instruments`)。
    start, end:
        取数窗 ``"YYYY-MM-DD"``;对齐 r27 的 FETCH_START/FETCH_END。
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)

    frames: List[pd.DataFrame] = []
    for code in codes:
        c = loader._read_bin(code, "close")
        a = loader._read_bin(code, "amount")
        if c is None or a is None:
            continue
        df = pd.DataFrame({"close": c, "amount": a})
        # r27 先 fetch[start,end] 再 pct_change ⇒ 窗内首日 ret = NaN(被丢)。
        df = df.loc[(df.index >= start_ts) & (df.index <= end_ts)]
        if df.empty:
            continue
        # 量纲校准(2026-06-12):amount=千元 批次 → ×1000(volume 仅作参照定标,
        # 不入面板;见 compute/units.py)。否则 total_amount/amt_resid 出假尖峰。
        df = normalize_frame_units(df, ref_vol=loader._read_bin(code, "volume"))
        # 对齐 qlib r27,两处口径必须一致才能逐位吻合:
        #  (1) 旧 pandas 的 groupby.pct_change() 默认 fill_method='ffill' —— 先把停牌
        #      断点(close=NaN)前值填充再算收益,使「复牌当日」可计入(新 pandas 默认
        #      fill_method=None 会让复牌日 ret=NaN 而漏掉 ~几十只);停牌当日 amount<=0
        #      仍被后面过滤,故只影响复牌日。
        #  (2) qlib ``$close`` 是 **float32**,而 ``_read_bin`` 已 astype(float64);在 9.5%
        #      涨停阈值上 float64 与 float32 会落到边界两侧(如 20→21.9 恰好 +9.5%:
        #      f32=0.09500003 计入 / f64=0.09499998 漏掉)。故 ret 显式走 float32 路径
        #      复刻 qlib 的舍入,使 limit_up_10 等整数计数逐位一致。
        df["ret"] = df["close"].astype("float32").ffill().pct_change(fill_method=None)
        frames.append(df)

    if not frames:
        raise RuntimeError("build_breadth_panel: 无任何可读股票(检查 provider_uri/窗口)")

    big = pd.concat(frames, axis=0)
    # 清洗:丢首日/断点 NaN ret + 停牌(amount<=0;NaN amount 同样被排除)
    big = big.dropna(subset=["ret"])
    big = big[big["amount"] > 0]

    # 向量化布尔列(等价 r27 的 lambda 聚合,避免逐组 apply 的慢路径)
    ret = big["ret"]
    big = big.assign(
        _up=(ret > 0),
        _dn=(ret < 0),
        _flat=(ret == 0),
        _lu10=((ret >= LIMIT_UP_10_LO) & (ret < LIMIT_UP_10_HI)),
        _lu20=(ret >= LIMIT_UP_20),
        _ld10=(ret <= LIMIT_DOWN_10),
    )

    grp = big.groupby(big.index)
    daily = grp.agg(
        total_amount=("amount", "sum"),
        stock_count=("close", "count"),
        up_count=("_up", "sum"),
        down_count=("_dn", "sum"),
        flat_count=("_flat", "sum"),
        limit_up_10=("_lu10", "sum"),
        limit_up_20=("_lu20", "sum"),
        limit_down_10=("_ld10", "sum"),
        mean_ret=("ret", "mean"),
        median_ret=("ret", "median"),
    ).sort_index()
    daily.index.name = "datetime"

    # 派生(对齐 r27)
    daily["up_ratio"] = daily["up_count"] / (daily["up_count"] + daily["down_count"])
    daily["limit_up_total"] = daily["limit_up_10"] + daily["limit_up_20"]
    daily["total_amount_yi"] = daily["total_amount"] / 1e8

    # 滚动分位(min_periods=20,对齐 r27)
    for win in ROLL_WINS:
        daily[f"amount_pct_{win}d"] = daily["total_amount"].rolling(win, min_periods=20).rank(pct=True)
        daily[f"upratio_pct_{win}d"] = daily["up_ratio"].rolling(win, min_periods=20).rank(pct=True)
        daily[f"lu_count_pct_{win}d"] = daily["limit_up_total"].rolling(win, min_periods=20).rank(pct=True)

    return daily


# ---------------------------------------------------------------------------
# 3) 残差化(对齐 ic_probe)
# ---------------------------------------------------------------------------
def compute_breadth_resid(panel: pd.DataFrame) -> pd.DataFrame:
    """60 日滚动 OLS 残差 + 60 日滚动百分位,返回 4 列(对齐 ic_probe 落盘列)。

    lu_resid = limit_up_total − fitted(limit_up_total ~ total_amount_yi) 当日残差;
    amt_resid = total_amount_yi − fitted(total_amount_yi ~ limit_up_total) 当日残差;
    *_pct60   = 各自 60 日滚动百分位 [0,1]。
    """
    from scipy import stats

    win = RESID_WIN
    idx = panel.index
    lu_resid = pd.Series(index=idx, dtype=float)
    amt_resid = pd.Series(index=idx, dtype=float)

    amt = panel["total_amount_yi"].values
    lut = panel["limit_up_total"].values
    n = len(panel)
    for i in range(win, n):
        lo = i - win
        x = amt[lo:i + 1]
        y = lut[lo:i + 1]
        if np.std(x) < 1e-6 or len(x) < win:
            continue
        slope, intercept, *_ = stats.linregress(x, y)
        lu_resid.iloc[i] = y[-1] - (slope * x[-1] + intercept)
        slope2, intercept2, *_ = stats.linregress(y, x)
        amt_resid.iloc[i] = x[-1] - (slope2 * y[-1] + intercept2)

    out = pd.DataFrame(index=idx)
    out["lu_resid"] = lu_resid
    out["amt_resid"] = amt_resid
    out["lu_resid_pct60"] = lu_resid.rolling(win).rank(pct=True)
    out["amt_resid_pct60"] = amt_resid.rolling(win).rank(pct=True)
    out.index.name = "datetime"
    return out[PANEL_COLS_RESID_OUT]


# ---------------------------------------------------------------------------
# 4) 编排:一次出 (panel, resid)
# ---------------------------------------------------------------------------
def build_breadth(
    provider_uri: str,
    start: str = "2019-11-01",
    end: str = "2026-06-05",
    codes: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """引擎原生构建宽度 (panel, resid)。``provider_uri`` = 引擎 day 根
    (如 ``"G:/stocks/stock_data/cn_data"``)。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader

    loader = QlibBinaryLoader(provider_uri)
    if codes is None:
        codes = list_all_instruments(provider_uri)
    panel = build_breadth_panel(loader, codes, start, end)
    resid = compute_breadth_resid(panel)
    return panel, resid
