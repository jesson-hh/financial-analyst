# -*- coding: utf-8 -*-
"""分钟级(5min)因子库 —— 「高频数据、低频因子」范式的本仓实现。

数据源:stock_data ``cn_data_5min``(2018-01 起),经引擎
``data.loader_factory.get_default_loader().fetch_quote(code, start, end, "5min")``
读取(与 seats 30min 研判同一条 PIT 读路径,零新增数据依赖)。

诚实口径(不装):
- 文献原式多用 1 分钟 bar(海通实证「频率越高越好」);本仓最细只有 5min
  (48 根/日),全部因子按 5min 实现并如实命名——若未来接入 1min,同代码直跑。
- 需要逐笔/成交笔数/L2 的因子(单笔金额切割、委托失衡)只能给**金额代理版**,
  名字带 ``_proxy``,与原研报因子不是同一个东西。
- APM 原版要求对指数收益回归取残差;这里先给**无指数中性的简化版**(直接
  隔夜-下午差的 t 统计),名字带 ``_simple``。

因子目录见 ``MINUTE_FACTORS``:每条含方向先验(dir:+1 高值看多 / -1 高值看空 /
0 无先验)、窗口、来源研报。计算入口 ``compute_factors(bars)``:输入一段日期
范围内的 5min bar DataFrame(须含 trade_date/open/high/low/close/volume,
amount 缺则以 close*volume 兜底),输出 date × factor 的日频因子面板。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

__all__ = ["MINUTE_FACTORS", "compute_daily_primitives", "compute_factors", "fetch_bars"]

_EPS = 1e-12


# ───────────────────────── 单日原语(每交易日一行) ─────────────────────────

def _day_primitives(day: pd.DataFrame) -> dict:
    """对单个交易日的 bar 序列(按时间升序)计算全部日内原语。"""
    n = len(day)
    o = day["open"].to_numpy(float)
    h = day["high"].to_numpy(float)
    lo = day["low"].to_numpy(float)
    c = day["close"].to_numpy(float)
    v = day["volume"].to_numpy(float)
    a = day["amount"].to_numpy(float)
    tot_a = a.sum()
    tot_v = v.sum()

    # 日内分钟收益(bar 间,首根用开盘价起算)
    prev = np.concatenate(([o[0]], c[:-1]))
    r = c / np.where(np.abs(prev) < _EPS, np.nan, prev) - 1.0
    r = np.where(np.isfinite(r), r, 0.0)

    rv = float((r ** 2).sum())
    rv_up = float((r[r > 0] ** 2).sum())
    rskew = float(math.sqrt(n) * (r ** 3).sum() / (rv ** 1.5)) if rv > _EPS else np.nan
    rkurt = float(n * (r ** 4).sum() / (rv ** 2)) if rv > _EPS else np.nan

    pv = np.nan
    if n >= 3 and np.std(c) > _EPS and np.std(v) > _EPS:
        pv = float(np.corrcoef(c, v)[0, 1])

    k = min(6, n)  # 5min×6 = 30 分钟
    head_share = float(a[:k].sum() / tot_a) if tot_a > _EPS else np.nan
    tail_share = float(a[-k:].sum() / tot_a) if tot_a > _EPS else np.nan
    open30 = float(c[k - 1] / o[0] - 1.0) if abs(o[0]) > _EPS else np.nan
    close30 = float(c[-1] / c[-k] - 1.0) if abs(c[-k]) > _EPS else np.nan
    trunc = float(c[-1] / c[k - 1] - 1.0) if abs(c[k - 1]) > _EPS else np.nan

    tt = (np.arange(n) + 0.5) / n
    vol_tc = float((tt * v).sum() / tot_v) if tot_v > _EPS else np.nan
    up_m, dn_m = r > 0, r < 0
    up_tc = float((tt[up_m] * r[up_m]).sum() / r[up_m].sum()) if up_m.any() else np.nan
    dn_tc = float((tt[dn_m] * (-r[dn_m])).sum() / (-r[dn_m]).sum()) if dn_m.any() else np.nan

    # 一字日(全天高=低)最高价时间无意义
    high_pos = float(np.argmax(h) / max(n - 1, 1)) if (h.max() - lo.min()) > _EPS else np.nan

    # 金额代理:大额 bar(金额前 30%)的收益和 / 流出 bar 平均金额比
    if n >= 4 and tot_a > _EPS:
        thr = np.quantile(a, 0.7)
        big_ret = float(r[a >= thr].sum())
        out_amt = a[dn_m]
        outflow_ratio = float(out_amt.mean() / a.mean()) if len(out_amt) else np.nan
    else:
        big_ret, outflow_ratio = np.nan, np.nan

    # 下午段(≥13:00)首 bar 开价 → 下午收益
    hours = day["trade_date"].dt.hour.to_numpy()
    pm_idx = np.nonzero(hours >= 13)[0]
    r_pm = np.nan
    if len(pm_idx) and abs(o[pm_idx[0]]) > _EPS:
        r_pm = float(c[-1] / o[pm_idx[0]] - 1.0)

    return {
        "open": float(o[0]), "close": float(c[-1]),
        "high": float(h.max()), "low": float(lo.min()),
        "amount": float(tot_a), "volume": float(tot_v),
        "vwap": float(tot_a / tot_v) if tot_v > _EPS else np.nan,
        "amp": float(h.max() / lo.min() - 1.0) if lo.min() > _EPS else np.nan,
        "mean_bar_amount": float(a.mean()),
        "rv": rv, "rv_up_share": (rv_up / rv) if rv > _EPS else np.nan,
        "rskew": rskew, "rkurt": rkurt, "pv_corr": pv,
        "head_share": head_share, "tail_share": tail_share,
        "open30_ret": open30, "close30_ret": close30, "trunc_ret": trunc,
        "vol_tc": vol_tc, "up_tc": up_tc, "dn_tc": dn_tc, "high_pos": high_pos,
        "big_amount_ret": big_ret, "outflow_amt_ratio": outflow_ratio,
        "r_pm": r_pm, "n_bars": n,
    }


def _normalize_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """列名归一化(qlib 5min 店的量列叫 ``vol``)+ amount 兜底 close*volume。"""
    df = bars.copy()
    if "volume" not in df.columns and "vol" in df.columns:
        df = df.rename(columns={"vol": "volume"})
    need = {"trade_date", "open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"5min bars 缺列: {sorted(missing)}")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    if "amount" not in df.columns or df["amount"].isna().all():
        df["amount"] = df["close"] * df["volume"]
    df["amount"] = df["amount"].fillna(df["close"] * df["volume"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df[df["close"].notna() & (df["volume"] > 0)]


def compute_daily_primitives(bars: pd.DataFrame) -> pd.DataFrame:
    """5min bar → 逐日日内原语面板(index=交易日 date)。"""
    df = _normalize_bars(bars)
    rows, idx = [], []
    for day, g in df.groupby(df["trade_date"].dt.date, sort=True):
        if len(g) < 4:      # 停牌/残日不计
            continue
        rows.append(_day_primitives(g))
        idx.append(pd.Timestamp(day))
    prims = pd.DataFrame(rows, index=pd.DatetimeIndex(idx, name="date"))
    if len(prims):
        prev_close = prims["close"].shift(1)
        prims["ret_d"] = prims["close"] / prev_close - 1.0
        prims["overnight_ret"] = prims["open"] / prev_close - 1.0
        prims["intraday_ret"] = prims["close"] / prims["open"] - 1.0
    return prims


# ───────────────────────── N 日聚合因子 ─────────────────────────

def _roll_mean(col, w):
    return lambda p, b: p[col].rolling(w, min_periods=max(3, w // 2)).mean()


def _roll_std(col, w):
    return lambda p, b: p[col].rolling(w, min_periods=max(3, w // 2)).std()


def _roll_sum(col, w):
    return lambda p, b: p[col].rolling(w, min_periods=max(3, w // 2)).sum()


def _cpv_trend(p, b, w=20):
    def slope(x):
        x = x[np.isfinite(x)]
        if len(x) < 5:
            return np.nan
        t = np.arange(len(x), dtype=float)
        return float(np.polyfit(t, x, 1)[0])
    return p["pv_corr"].rolling(w, min_periods=10).apply(slope, raw=True)


def _ideal_amp(p, b, w=20, q=0.25):
    def f(win: pd.DataFrame):
        win = win.dropna(subset=["close", "amp"])
        k = max(int(len(win) * q), 1)
        srt = win.sort_values("close")
        return srt["amp"].tail(k).mean() - srt["amp"].head(k).mean()
    out = pd.Series(np.nan, index=p.index)
    for i in range(len(p)):
        if i + 1 >= 10:
            out.iloc[i] = f(p.iloc[max(0, i + 1 - w): i + 1])
    return out


def _ideal_rev_proxy(p, b, w=20):
    """理想反转 M 的金额代理:按「平均单bar金额」排序切割(原版按单笔金额,须逐笔数据)。"""
    def f(win: pd.DataFrame):
        win = win.dropna(subset=["mean_bar_amount", "ret_d"])
        if len(win) < 10:
            return np.nan
        k = len(win) // 2
        srt = win.sort_values("mean_bar_amount")
        return srt["ret_d"].tail(k).sum() - srt["ret_d"].head(k).sum()
    out = pd.Series(np.nan, index=p.index)
    for i in range(len(p)):
        if i + 1 >= 10:
            out.iloc[i] = f(p.iloc[max(0, i + 1 - w): i + 1])
    return out


def _apb(p, b, w=20):
    """APB = ln( N日均价的简单均值 / 成交量加权均值 )(东方证券)。>0 = 量集中在低价处。"""
    def f(win: pd.DataFrame):
        win = win.dropna(subset=["vwap", "volume"])
        if len(win) < 3 or (win["vwap"] * win["volume"]).sum() <= 0:
            return np.nan
        vw = (win["vwap"] * win["volume"]).sum() / win["volume"].sum()
        return math.log(win["vwap"].mean() / vw)
    out = pd.Series(np.nan, index=p.index)
    for i in range(len(p)):
        if i + 1 >= 3:
            out.iloc[i] = f(p.iloc[max(0, i + 1 - w): i + 1])
    return out


def _apm_simple(p, b, w=20):
    """APM 简化版:δ=隔夜收益-下午收益,stat=均值/(σ/√N)。无指数中性(原版需回归残差)。"""
    pm_ret = p["r_pm"]
    delta = p["overnight_ret"] - pm_ret

    def f(x):
        x = x[np.isfinite(x)]
        if len(x) < 10 or x.std(ddof=1) < _EPS:
            return np.nan
        return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))
    return delta.rolling(w, min_periods=10).apply(lambda x: f(pd.Series(x)), raw=True)


def _smart_q(p, b, w=10, beta=0.25, top=0.2):
    """聪明钱 Q(开源2.0):S=|r|/V^β 降序取量占比前 top 的「聪明段」,Q=VWAP_smart/VWAP_all。"""
    df = _normalize_bars(b)
    df["day"] = df["trade_date"].dt.normalize()
    prev = df.groupby("day")["close"].shift(1)
    first_open = df.groupby("day")["open"].transform("first")
    base = prev.fillna(first_open)
    df["r"] = (df["close"] / base - 1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    days = sorted(df["day"].unique())
    pos = {d: i for i, d in enumerate(days)}
    out = pd.Series(np.nan, index=p.index)
    for d in p.index:
        dd = pd.Timestamp(d).normalize()
        if dd not in pos:
            continue
        i = pos[dd]
        sel = df[df["day"].isin(days[max(0, i - w + 1): i + 1])]
        if len(sel) < 24:
            continue
        s = sel["r"].abs() / np.power(sel["volume"].clip(lower=1.0), beta)
        sel = sel.assign(_s=s).sort_values("_s", ascending=False)
        cum = sel["volume"].cumsum() / sel["volume"].sum()
        smart = sel[cum <= top]
        if len(smart) == 0 or smart["volume"].sum() <= 0:
            continue
        vwap_smart = smart["amount"].sum() / smart["volume"].sum()
        vwap_all = sel["amount"].sum() / sel["volume"].sum()
        if vwap_all > _EPS:
            out.loc[d] = float(vwap_smart / vwap_all)
    return out


def _tgd_proxy(p, b, w=20):
    """TGD 简化:mean(跌幅时间重心-涨幅时间重心)。原版须截面回归残差,单票语境退化为差值。"""
    return (p["dn_tc"] - p["up_tc"]).rolling(w, min_periods=10).mean()


@dataclass
class MinuteFactor:
    name: str
    direction: int          # +1 高看多 / -1 高看空 / 0 无先验
    window: int
    source: str
    desc: str
    fn: Callable = field(repr=False, default=None)


MINUTE_FACTORS: list[MinuteFactor] = [
    MinuteFactor("gl_min_smart_q", -1, 10, "开源·市场微观结构(3) 聪明钱2.0",
                 "S=|r|/V^0.25 排序取量占比前20%的VWAP/全段VWAP;高=聪明钱高位成交=出货", _smart_q),
    MinuteFactor("gl_min_apb5", +1, 5, "东方证券·APB",
                 "ln(5日均价均值/量加权均值);>0=量在低价=买压", lambda p, b: _apb(p, b, 5)),
    MinuteFactor("gl_min_apb20", +1, 20, "东方证券·APB", "20日窗APB", lambda p, b: _apb(p, b, 20)),
    MinuteFactor("gl_min_ideal_amp", -1, 20, "开源·理想振幅V",
                 "收盘最高25%日振幅均值-最低25%日振幅均值;高位放振幅=出货", _ideal_amp),
    MinuteFactor("gl_min_ideal_rev_proxy", -1, 20, "开源·理想反转M(金额代理)",
                 "按bar均额切割的高额半-低额半涨幅差;原版须逐笔单笔金额", _ideal_rev_proxy),
    MinuteFactor("gl_min_cpv_avg", -1, 20, "东吴·CPV", "日内量价相关系数20日均值", _roll_mean("pv_corr", 20)),
    MinuteFactor("gl_min_cpv_std", -1, 20, "东吴·CPV", "日内量价相关系数20日std", _roll_std("pv_corr", 20)),
    MinuteFactor("gl_min_cpv_trend", -1, 20, "东吴·CPV", "日内量价相关系数20日斜率", _cpv_trend),
    MinuteFactor("gl_min_rv", 0, 20, "海通·已实现波动", "RV=Σr²的20日均值(单独无选股力,拆分才有效)",
                 _roll_mean("rv", 20)),
    MinuteFactor("gl_min_rskew", -1, 20, "海通·高频偏度", "日内收益偏度20日均值;高偏度=博彩股看空",
                 _roll_mean("rskew", 20)),
    MinuteFactor("gl_min_rkurt", 0, 20, "海通·高频峰度", "日内收益峰度20日均值(实证无显著选股力)",
                 _roll_mean("rkurt", 20)),
    MinuteFactor("gl_min_rv_up_share", -1, 20, "海通·波动分解(25)",
                 "上行波动占比RV+/RV 20日均值;上行波动大=冲高型看空", _roll_mean("rv_up_share", 20)),
    MinuteFactor("gl_min_tail_share", +1, 20, "海通·尾盘成交占比",
                 "14:30后成交额占比20日均值;知情者偏好尾盘", _roll_mean("tail_share", 20)),
    MinuteFactor("gl_min_head_share", -1, 20, "中金/华泰·开盘成交占比",
                 "开盘30分钟成交额占比20日均值;开盘放量=隔夜信息过度反应", _roll_mean("head_share", 20)),
    MinuteFactor("gl_min_head_share_std", -1, 20, "中金·trade_headRatio_std",
                 "开盘成交占比20日std", _roll_std("head_share", 20)),
    MinuteFactor("gl_min_open30_mom", +1, 20, "中信·日内分时(动量项)",
                 "开盘30分钟收益20日均值;开盘段是信息定价有趋势性", _roll_mean("open30_ret", 20)),
    MinuteFactor("gl_min_close30_rev", -1, 20, "中信/民生·日内分时(反转项)",
                 "尾盘30分钟收益20日均值;尾盘是行为性交易反转项", _roll_mean("close30_ret", 20)),
    MinuteFactor("gl_min_trunc_rev", -1, 20, "海通·改进反转",
                 "剔隔夜+开盘30分钟的日内截断收益20日累计;高=行为性上涨=反转看空", _roll_sum("trunc_ret", 20)),
    MinuteFactor("gl_min_overnight_sum", 0, 20, "中信建投·隔夜-日内异象",
                 "隔夜收益20日累计;A股隔夜平均为负,拔河效应方向复杂", _roll_sum("overnight_ret", 20)),
    MinuteFactor("gl_min_intraday_sum", -1, 20, "学术·隔夜/日内分解",
                 "日内收益20日累计;日内涨幅贡献反转", _roll_sum("intraday_ret", 20)),
    MinuteFactor("gl_min_apm_simple", +1, 20, "开源·APM进阶(简化无指数中性)",
                 "δ=隔夜-下午收益差的t统计;知情者行为在隔夜/上午", _apm_simple),
    MinuteFactor("gl_min_vol_time_center", +1, 20, "时间重心族",
                 "成交量时间重心20日均值;重心晚≈尾盘占比高", _roll_mean("vol_tc", 20)),
    MinuteFactor("gl_min_tgd_proxy", +1, 20, "开源·TGD(截面残差退化为差值)",
                 "跌幅重心-涨幅重心20日均值;下跌集中尾盘=行为性下跌=反转看多", _tgd_proxy),
    MinuteFactor("gl_min_high_time_pos", -1, 20, "日内特征·最高价出现时间",
                 "日内最高价时间位置20日均值(一字日剔除);最高价出现晚反而更差", _roll_mean("high_pos", 20)),
    MinuteFactor("gl_min_big_amount_ret", -1, 20, "海通·大单推动涨幅(金额代理)",
                 "金额前30%bar的收益和20日均值;大额博弈段涨幅反转最强", _roll_mean("big_amount_ret", 20)),
    MinuteFactor("gl_min_outflow_amt_ratio", +1, 20, "海通·单笔流出金额占比(金额代理)",
                 "下跌bar均额/全体bar均额20日均值;下跌时大额=大资金逢低承接", _roll_mean("outflow_amt_ratio", 20)),
]


def compute_factors(bars: pd.DataFrame,
                    factors: Optional[list[str]] = None) -> pd.DataFrame:
    """5min bar → 日频因子面板(index=date, columns=因子名)。"""
    prims = compute_daily_primitives(bars)
    if not len(prims):
        return pd.DataFrame()
    want = set(factors) if factors else None
    out = {}
    for spec in MINUTE_FACTORS:
        if want is not None and spec.name not in want:
            continue
        try:
            out[spec.name] = spec.fn(prims, bars)
        except Exception as exc:  # noqa: BLE001  单因子失败不拖垮面板,诚实置 NaN
            out[spec.name] = pd.Series(np.nan, index=prims.index)
            out[spec.name].attrs["error"] = f"{type(exc).__name__}: {exc}"
    return pd.DataFrame(out, index=prims.index)


def fetch_bars(code: str, start: str, end: str) -> pd.DataFrame:
    """经引擎默认 loader 取 5min bar(与 seats 同一读路径;引擎缺席时诚实抛错)。"""
    from financial_analyst.data import loader_factory as _lf
    loader = _lf.get_default_loader()
    df = loader.fetch_quote(code, start, end, "5min")
    if df is None or not len(df):
        raise RuntimeError(f"cn_data_5min 无数据: {code} {start}..{end}")
    return df
