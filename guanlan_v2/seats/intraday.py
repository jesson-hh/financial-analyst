# -*- coding: utf-8 -*-
"""盘中实时日型判定 —— WatchFeed 当日 5min bar → 判别器特征 → logit 打分(advisory only)。

链路:引擎 pytdx ``WatchFeed.bars5``(实时,~240根≈5个会话)出当日与近日 5min bar;
历史同段量(开盘30分量比的分母)走 5min 历史库(滞后数日可容忍——取最近可得20个会话);
市场代理 = 固定十票篮子的截面中位(TTL 缓存 5 分钟);prev_* 特征来自调用方的日线 df
(严格取早于今日的行)。模型 = ``daytype_model.json``(train-only 定标:阈值取 train 分位;
OOS 实测 震荡精度 82.1%@覆盖30%,AUC 0.685——历史口径,非未来保证)。

PIT 红线:本模块只该在「研判日 == 今天」的实盘语境被调用(接线处守卫);
历史回放绝不注入实时数据。一切失败诚实返回 None/缺席,不抛不编。
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

__all__ = ["build_intraday_context", "score_daytype", "render_daytype_lines"]

_MODEL_PATH = Path(__file__).resolve().parent / "daytype_model.json"
_MODEL_CACHE: dict = {}
_BASKET = ["SH600519", "SZ300750", "SH601318", "SH600036", "SZ000858",
           "SZ002594", "SH601012", "SZ300308", "SH600900", "SH601899"]
_BASKET_CACHE: dict = {"ts": 0.0, "val": None}
_BASKET_TTL = 300.0


def _load_model() -> dict:
    if not _MODEL_CACHE:
        _MODEL_CACHE.update(json.loads(_MODEL_PATH.read_text(encoding="utf-8")))
    return _MODEL_CACHE


def _fetch_bars5(code: str, n: int = 480) -> Optional[pd.DataFrame]:
    """WatchFeed 实时 5min(失败→None)。列归一:trade_date/open/high/low/close/volume。"""
    try:
        from financial_analyst.watch.feed import WatchFeed
        df = WatchFeed().bars5(code, n)
    except Exception:  # noqa: BLE001
        return None
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns={"vol": "volume"}).copy()
    if "volume" not in df.columns:
        return None
    # 单位对齐:WatchFeed vol 为「手」,5min 历史库为「股」——量比分母来自历史库,
    # 此处 ×100 统一为股(实机实测不齐会差百倍,2026-08-10 300750 量比曾显 0.01)。
    df["volume"] = df["volume"] * 100.0
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df[df["close"].notna() & (df["volume"] >= 0)].sort_values("trade_date")


def _sessions(df: pd.DataFrame) -> dict:
    return dict(tuple(df.groupby(df["trade_date"].dt.normalize())))


def _open30(g: pd.DataFrame) -> Optional[dict]:
    """一个会话的开盘30分钟读数(不足6根5min bar → None=太早)。"""
    if len(g) < 6:
        return None
    o = float(g["open"].iloc[0])
    if o <= 0:
        return None
    c6 = float(g["close"].iloc[5])
    return {"open": o, "ret": c6 / o - 1.0,
            "range": (float(g["high"].iloc[:6].max()) - float(g["low"].iloc[:6].min())) / o,
            "vol": float(g["volume"].iloc[:6].sum())}


def _basket_snapshot() -> Optional[dict]:
    """市场代理:篮子截面中位 |开盘30分| 与中位缺口(缺口用帧内上一会话收盘)。"""
    now = time.time()
    if _BASKET_CACHE["val"] is not None and now - _BASKET_CACHE["ts"] < _BASKET_TTL:
        return _BASKET_CACHE["val"]
    rets, gaps = [], []
    for b in _BASKET:
        df = _fetch_bars5(b, 480)
        if df is None:
            continue
        ses = _sessions(df)
        days = sorted(ses)
        if not days:
            continue
        o30 = _open30(ses[days[-1]].reset_index(drop=True))
        if o30 is None:
            continue
        rets.append(abs(o30["ret"]))
        if len(days) >= 2:
            pc = float(ses[days[-2]]["close"].iloc[-1])
            if pc > 0:
                gaps.append(o30["open"] / pc - 1.0)
    out = None
    if len(rets) >= 5:
        out = {"mkt_abs_open30": float(np.median(rets)),
               "mkt_gap": float(np.median(gaps)) if gaps else 0.0,
               "n_basket": len(rets)}
    _BASKET_CACHE.update({"ts": now, "val": out})
    return out


def _hist_open30_volmean(code: str, today: pd.Timestamp) -> Optional[float]:
    """前20个可得会话的开盘30分量均值(5min 历史库,滞后数日可容忍)。"""
    try:
        from financial_analyst.data import loader_factory as _lf
        start = (today - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        end = (today - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        h = _lf.get_default_loader().fetch_quote(code, start, end, "5min")
    except Exception:  # noqa: BLE001
        return None
    if h is None or len(h) == 0:
        return None
    h = h.rename(columns={"vol": "volume"})
    h["trade_date"] = pd.to_datetime(h["trade_date"])
    vols = []
    for _, g in h.groupby(h["trade_date"].dt.normalize()):
        g = g.sort_values("trade_date")
        if len(g) >= 6:
            vols.append(float(g["volume"].iloc[:6].sum()))
    if len(vols) < 8:
        return None
    return float(np.mean(vols[-20:]))


def build_intraday_context(code: str, daily_df: Optional[pd.DataFrame]) -> Optional[dict]:
    """实时日型判定入口:code + 日线 df(prev_* 特征来源)→ {features, score, notes} 或 None。
    只该在「研判日==今天」的实盘语境调用(PIT 守卫在接线处)。"""
    df5 = _fetch_bars5(code, 480)
    if df5 is None:
        return None
    ses = _sessions(df5)
    days = sorted(ses)
    if not days:
        return None
    today = days[-1]
    g = ses[today].reset_index(drop=True)
    o30 = _open30(g)
    if o30 is None:
        return {"too_early": True, "session": str(today.date())}
    notes: list[str] = []
    # 缺口:帧内上一会话收盘,退而日线 df
    prev_close = None
    if len(days) >= 2:
        prev_close = float(ses[days[-2]]["close"].iloc[-1])
    feats: dict = {}
    is20 = code[2:].startswith("30") or code[2:].startswith("68")
    feats["is20cm"] = float(is20)
    feats["open30_ret"] = o30["ret"]
    feats["abs_open30"] = abs(o30["ret"])
    feats["open30_range"] = o30["range"]
    # 历史同段量
    vh = _hist_open30_volmean(code, today)
    if vh and vh > 0:
        feats["open30_volratio"] = o30["vol"] / vh
    else:
        notes.append("开盘量比缺历史分母(5min库不可得),该特征按训练均值填补")
    # prev_* 自日线(严格早于今日)
    if daily_df is not None and len(daily_df) >= 25:
        d = daily_df.copy()
        dcol = "trade_date" if "trade_date" in d.columns else "date"
        d[dcol] = pd.to_datetime(d[dcol])
        d = d[d[dcol] < today].sort_values(dcol)
        if len(d) >= 22:
            amp = d["high"] / d["low"] - 1.0
            ret = d["close"].pct_change()
            trend = (d["close"] / d["open"] - 1.0).abs() > 0.6 * amp
            lim = 0.19 if is20 else 0.095
            v20 = float(d["volume" if "volume" in d.columns else "vol"].tail(21).head(20).mean())
            vprev = float(d["volume" if "volume" in d.columns else "vol"].iloc[-1])
            feats.update({
                "prev_trend": float(bool(trend.iloc[-1])),
                "prev_ret": float(ret.iloc[-1]) if np.isfinite(ret.iloc[-1]) else 0.0,
                "abs_prev_ret": abs(float(ret.iloc[-1])) if np.isfinite(ret.iloc[-1]) else 0.0,
                "prev_amp": float(amp.iloc[-1]),
                "amp20_med": float(amp.tail(20).median()),
                "prev_volratio": (vprev / v20) if v20 > 0 else np.nan,
                "prev_limit": float(np.isfinite(ret.iloc[-1]) and abs(ret.iloc[-1]) >= lim),
            })
            if prev_close is None:
                prev_close = float(d["close"].iloc[-1])
    else:
        notes.append("日线 df 不足,prev_* 特征按训练均值填补")
    if prev_close and prev_close > 0:
        feats["gap"] = o30["open"] / prev_close - 1.0
        feats["abs_gap"] = abs(feats["gap"])
    else:
        notes.append("缺昨收,缺口特征按训练均值填补")
    mkt = _basket_snapshot()
    if mkt:
        feats["mkt_abs_open30"] = mkt["mkt_abs_open30"]
        feats["mkt_gap"] = mkt["mkt_gap"]
    else:
        notes.append("市场篮子代理不可得,按训练均值填补")
    score = score_daytype(feats)
    return {"session": str(today.date()), "features": feats, "score": score, "notes": notes}


def score_daytype(feats: dict) -> dict:
    """logit 打分:缺失特征以训练均值填补(标准化后=0)并如实列出;缺失>5 不判定。"""
    m = _load_model()
    xs, missing = [], []
    for i, f in enumerate(m["features"]):
        v = feats.get(f)
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            missing.append(f)
            xs.append(0.0)
        else:
            xs.append((float(v) - m["mean"][i]) / m["scale"][i])
    if len(missing) > 5:
        return {"ok": False, "reason": f"特征缺失过多({len(missing)}/16),不判定", "missing": missing}
    z = m["intercept"] + float(np.dot(xs, m["coef"]))
    p = 1.0 / (1.0 + math.exp(-z))
    if p <= m["thr_cov30_train"]:
        tier, verdict = "T30", "做T窗口(最像震荡日的30%档)"
    elif p <= m["thr_cov50_train"]:
        tier, verdict = "T50", "弱可做(50%档,谨慎)"
    else:
        tier, verdict = "NO", "禁做T(单边风险档)"
    return {"ok": True, "p_trend": round(p, 4), "tier": tier, "verdict": verdict,
            "missing": missing,
            "oos_note": f"历史OOS:震荡精度{m['oos_metrics']['range_precision_cov30']:.0%}@覆盖30%(非未来保证)"}


def render_daytype_lines(ctx: dict) -> str:
    """渲染为研判证据行。"""
    if ctx.get("too_early"):
        return f"日型判定:开盘未满30分钟({ctx.get('session')}),暂不判定——10:00 后再看"
    s = ctx.get("score") or {}
    if not s.get("ok"):
        return f"日型判定:{s.get('reason', '不可用')}"
    f = ctx.get("features", {})
    bits = []
    if "gap" in f:
        bits.append(f"缺口{f['gap']:+.1%}")
    bits.append(f"开盘30分{f.get('open30_ret', 0):+.1%}")
    if "open30_volratio" in f:
        bits.append(f"同段量比{f['open30_volratio']:.2f}")
    if "mkt_abs_open30" in f:
        bits.append(f"市场|开盘|中位{f['mkt_abs_open30']:.1%}")
    line = (f"日型判定(模型v1·{ctx.get('session')}):p(单边)={s['p_trend']:.2f} → **{s['verdict']}**"
            f" · {' '.join(bits)} · {s['oos_note']}")
    extras = list(ctx.get("notes") or [])
    if s.get("missing"):
        extras.append("填补特征:" + ",".join(s["missing"]))
    if extras:
        line += "\n判定降级注记:" + ";".join(extras)
    return line
