# -*- coding: utf-8 -*-
"""用本地 5 分钟历史数据研究预警阈值；只返回建议，不创建或启用规则。"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any, Optional

from guanlan_v2.datafeed.local_query import local_query_loader
from .core import normalize_code


_MAX_RESEARCH_DAYS = 365


def _research_period(start: str, end: str) -> tuple[str, str]:
    """Reject malformed/unbounded requests before resolving any data source."""
    def parse(value: str, field: str) -> date:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{field} 必须为有效日期 YYYY-MM-DD")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} 必须为有效日期 YYYY-MM-DD") from exc

    last = parse(end, "end") if end else date.today()
    try:
        first = parse(start, "start") if start else last - timedelta(days=180)
    except OverflowError as exc:
        raise ValueError("end 太早，无法构造默认 180 天研究窗口") from exc
    if last > date.today():
        raise ValueError("end 不能晚于今天，历史研究不使用未来日期")
    if first > last:
        raise ValueError("start 不能晚于 end")
    if (last - first).days > _MAX_RESEARCH_DAYS:
        raise ValueError(f"预警历史研究窗口最多 {_MAX_RESEARCH_DAYS} 天，请缩小 start/end")
    return first.isoformat(), last.isoformat()


def _events(condition, cooldown_bars: int = 6):
    hits = []
    last = -10**9
    previous = False
    for i, value in enumerate(condition.fillna(False).astype(bool).tolist()):
        if value and not previous and i - last >= cooldown_bars:
            hits.append(i)
            last = i
        previous = value
    return hits


def _stats(signal, forward, *, split: float = 0.7) -> dict[str, Any]:
    import numpy as np

    indexes = _events(signal)
    rows = [float(forward.iloc[i]) for i in indexes
            if i < len(forward) and np.isfinite(forward.iloc[i])]
    cut = int(len(forward) * split)
    oos = [float(forward.iloc[i]) for i in indexes
           if i >= cut and i < len(forward) and np.isfinite(forward.iloc[i])]

    def one(values):
        if not values:
            return {"events": 0, "median_forward_pct": None,
                    "continued_drop_rate": None, "mean_forward_pct": None}
        a = np.asarray(values, dtype=float)
        return {"events": int(a.size), "median_forward_pct": round(float(np.median(a)), 4),
                "mean_forward_pct": round(float(a.mean()), 4),
                "continued_drop_rate": round(float((a < 0).mean()), 4)}

    return {"all": one(rows), "oos": one(oos), "split": "chronological_70_30"}


def _load_5m(code: str, start: str, end: str):
    frame = local_query_loader(freq="5min").fetch_quote(code, start, end, "5min")
    if frame is None or len(frame) == 0:
        return None
    import pandas as pd

    df = frame.copy()
    ts_col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).drop_duplicates(ts_col).set_index(ts_col)
    for col in ("close", "high"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def research_alert(*, scope: str = "market", code: str = "", start: str = "",
                   end: str = "", window_minutes: int = 15,
                   threshold_pct: Optional[float] = None, down_ratio_min: float = 0.7,
                   forward_minutes: int = 30, max_codes: int = 60) -> dict[str, Any]:
    """Historical event study. A suggested_rule is a draft, never an activated monitor."""
    import pandas as pd

    scope = str(scope or "market").lower()
    try:
        start, end = _research_period(start, end)
    except ValueError as exc:
        return {"ok": False, "scope": scope, "reason": str(exc)}
    window_minutes = min(240, max(5, int(window_minutes)))
    forward_minutes = min(240, max(5, int(forward_minutes)))
    forward_bars = max(1, forward_minutes // 5)
    if scope == "stock":
        try:
            canon = normalize_code(code)
        except ValueError as exc:
            return {"ok": False, "scope": scope, "reason": str(exc)}
        threshold = float(-3.0 if threshold_pct is None else threshold_pct)
        try:
            df = _load_5m(canon, start, end)
        except Exception as exc:
            return {"ok": False, "scope": scope, "code": canon,
                    "source": "local_qlib_5min", "period": {"start": start, "end": end},
                    "reason": f"本地 5 分钟数据不可用: {type(exc).__name__}: {exc}"}
        if df is None or len(df) < 100:
            return {"ok": False, "reason": "可用 5 分钟历史不足 100 根，不能给出可靠建议",
                    "scope": scope, "code": canon}
        daily_high = df["high"].groupby(df.index.date).cummax()
        drawdown = (df["close"] / daily_high - 1.0) * 100.0
        signal = drawdown <= threshold
        forward = (df["close"].shift(-forward_bars) / df["close"] - 1.0) * 100.0
        stats = _stats(signal, forward)
        return {"ok": True, "scope": scope, "period": {"start": start, "end": end},
                "source": "local_qlib_5min",
                "bars": int(len(df)), "metric": "intraday_drawdown_from_high_pct",
                "threshold_pct": threshold, "forward_minutes": forward_minutes,
                "event_study": stats,
                "suggested_rule": {"name": f"{canon} 盘中高点回撤预警",
                                   "kind": "stock_drawdown_from_high_pct", "code": canon,
                                   "threshold": threshold, "hysteresis": 0.5,
                                   "cooldown_minutes": 30, "enabled": False,
                                   "note": f"历史研究 {start}~{end}；创建后仍需人工启用"}}
    if scope != "market":
        return {"ok": False, "reason": "scope 只支持 market 或 stock"}

    from financial_analyst.data.universe import resolve_universe_codes

    threshold = float(-1.0 if threshold_pct is None else threshold_pct)
    codes = [normalize_code(x) for x in resolve_universe_codes("csi300_active")[:min(120, max(20, int(max_codes)))]]
    closes = []
    failures = 0
    source_errors = []
    for symbol in codes:
        try:
            df = _load_5m(symbol, start, end)
            if df is None or len(df) < 100:
                failures += 1
                continue
            closes.append(df["close"].rename(symbol))
        except Exception as exc:
            failures += 1
            if len(source_errors) < 3:
                source_errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
    if len(closes) < 10:
        return {"ok": False, "reason": "全盘代理有效证券不足 10 只，不能研究",
                "source": "local_qlib_5min", "source_errors": source_errors,
                "requested_codes": len(codes), "loaded_codes": len(closes)}
    panel = pd.concat(closes, axis=1).sort_index()
    bars = max(1, window_minutes // 5)
    returns = (panel / panel.shift(bars) - 1.0) * 100.0
    valid = returns.notna().sum(axis=1)
    proxy = returns.mean(axis=1).where(valid >= max(10, int(len(closes) * 0.6)))
    down_ratio = (returns < 0).sum(axis=1).div(valid).where(valid > 0)
    signal = (proxy <= threshold) & (down_ratio >= float(down_ratio_min))
    forward = (panel.shift(-forward_bars) / panel - 1.0).mean(axis=1) * 100.0
    stats = _stats(signal, forward)
    return {"ok": True, "scope": scope, "period": {"start": start, "end": end},
            "source": "local_qlib_5min",
            "universe": "csi300_active", "requested_codes": len(codes),
            "loaded_codes": len(closes), "failed_codes": failures,
            "aligned_bars": int(proxy.notna().sum()), "window_minutes": window_minutes,
            "threshold_pct": threshold, "down_ratio_min": float(down_ratio_min),
            "forward_minutes": forward_minutes, "event_study": stats,
            "suggested_rule": {"name": f"全盘 {window_minutes} 分钟跳水预警",
                               "kind": "market_proxy_drop_window_pct",
                               "threshold": threshold, "window_minutes": window_minutes,
                               "down_ratio_min": float(down_ratio_min),
                               "universe": "csi300_active", "sample_size": len(closes),
                               "hysteresis": 0.3, "cooldown_minutes": 30, "enabled": False,
                               "note": f"历史研究 {start}~{end}；等权代理非官方指数；创建后仍需人工启用"}}
