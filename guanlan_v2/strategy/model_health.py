# -*- coding: utf-8 -*-
"""模型体检 —— v4 LGB 排名分的健康度量(衰减监控)+ 真 OOS 底仓积累。

三个产物(全在 vendor/artifacts/,regen 顺带更新,原子写):

1. ``model_health.parquet``(date/ic/asof/note):**同模型整窗回看** IC 序列 ——
   regen 刚训完的模型对近 ~60 个**有标签**交易日(标签=真实未来5日收益)逐日截面 rank-IC。
   ⚠ 口径诚实:这些交易日在训练窗内 → **偏乐观,非 OOS 绝对值**;用途是**衰减趋势**监控
   (模型对最近市场的拟合是否在退化),不是收益预估。
2. ``model_score_history.parquet``(date/code/lgb_pct):**逐日快照积累**——每次 regen 把当日
   全市场 lgb_pct 存档(按 date 去重,重跑同日覆盖)。这是真 OOS 的底仓:快照分数在
   预测时点冻结,未来收益实现后即可算**不含任何回看偏差**的 vintage IC。
3. ``model_vintage_ic.parquet``(date/ic/n):**真 OOS vintage IC**——对已实现 fwd5 的历史快照日
   增量计算(spearman(快照 lgb_pct, 真实未来5日收益));积累 ≥10 天后体检卡切真 OOS 口径。

serving 端只读摘要(`load_health_summary`,纯 pandas);计算端(vintage 需引擎 close bins)
只在 regen 子进程跑。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from guanlan_v2.strategy.paths import ARTIFACTS_DIR

MODEL_HEALTH_PARQUET = ARTIFACTS_DIR / "model_health.parquet"
SCORE_HISTORY_PARQUET = ARTIFACTS_DIR / "model_score_history.parquet"
VINTAGE_IC_PARQUET = ARTIFACTS_DIR / "model_vintage_ic.parquet"

_BACKCAST_NOTE = "同模型整窗回看(训练样本内,偏乐观);仅作衰减趋势监控,非OOS绝对值"


def _atomic(df, path, **kw) -> None:
    tmp = str(path) + ".tmp"
    df.to_parquet(tmp, **kw)
    os.replace(tmp, str(path))


# ───────────────────────────── regen 写入端 ─────────────────────────────

def write_backcast(ic_series: List, asof: str) -> int:
    """build_v4 health 出参的 ic_series [(date,ic)...] → model_health.parquet。"""
    import pandas as pd
    df = pd.DataFrame(ic_series, columns=["date", "ic"])
    df["asof"] = asof
    df["note"] = _BACKCAST_NOTE
    _atomic(df, MODEL_HEALTH_PARQUET, index=False)
    return len(df)


def append_score_history(v4out, end: str) -> int:
    """当日快照(date/code/lgb_pct)入档;按 date 去重(重跑同日=覆盖)。返回档内快照日数。"""
    import pandas as pd
    snap = v4out[["code", "lgb_pct"]].copy()
    snap["date"] = str(end)
    if SCORE_HISTORY_PARQUET.exists():
        hist = pd.read_parquet(SCORE_HISTORY_PARQUET)
        hist = hist[hist["date"] != str(end)]
        snap = pd.concat([hist, snap], ignore_index=True)
    _atomic(snap, SCORE_HISTORY_PARQUET, index=False)
    return int(snap["date"].nunique())


def update_vintage_ic(provider_uri: str, horizon: int = 5) -> int:
    """对**已实现且未算过**的快照日增量算真 OOS IC。返回 vintage 表总行数。

    实现判据:快照日在交易日历中,且其后 ≥horizon 个交易日已有数据(用实际数据日界定)。
    每只票只取快照日与 D+horizon 两点 close(_read_bin 整列读,逐票循环 ≈ breadth 单遍成本,
    通常每次 regen 只新增 1 个快照日)。
    """
    import pandas as pd
    if not SCORE_HISTORY_PARQUET.exists():
        return 0
    hist = pd.read_parquet(SCORE_HISTORY_PARQUET)
    done = set()
    rows: List[Dict[str, Any]] = []
    if VINTAGE_IC_PARQUET.exists():
        old = pd.read_parquet(VINTAGE_IC_PARQUET)
        done = set(old["date"].astype(str))
        rows = old.to_dict("records")

    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    ld = QlibBinaryLoader(provider_uri)
    # 真·最新数据日(日历预排到年底,不能用 cal[-1];同 regen._latest_trade_date 口径)
    probe = ld._read_bin("SH600519", "close")
    if probe is None or probe.dropna().empty:
        return len(rows)
    last_data = pd.Timestamp(probe.dropna().index[-1])
    cal = pd.DatetimeIndex([d for d in ld._load_calendar("day") if pd.Timestamp(d) <= last_data])

    pend = []
    for d in sorted(hist["date"].astype(str).unique()):
        if d in done:
            continue
        ts = pd.Timestamp(d)
        pos = cal.searchsorted(ts)
        if pos < len(cal) and cal[pos] == ts and pos + horizon < len(cal):
            pend.append((d, ts, cal[pos + horizon]))
    for d, t0, t1 in pend:
        snap = hist[hist["date"] == d]
        fwd = {}
        for code in snap["code"]:
            s = ld._read_bin(str(code), "close")
            if s is None:
                continue
            try:
                c0, c1 = s.get(t0), s.get(t1)
            except Exception:  # noqa: BLE001
                continue
            if c0 and c1 and pd.notna(c0) and pd.notna(c1) and float(c0) > 0:
                fwd[str(code)] = float(c1) / float(c0) - 1.0
        sub = snap[snap["code"].astype(str).isin(fwd)].copy()
        if len(sub) < 100:      # 截面太薄不算(诚实缺席)
            continue
        sub["fwd"] = sub["code"].astype(str).map(fwd)
        ic = float(sub["lgb_pct"].rank().corr(sub["fwd"].rank()))
        rows.append({"date": d, "ic": ic, "n": int(len(sub))})
    if rows:
        out = pd.DataFrame(rows).sort_values("date")
        _atomic(out, VINTAGE_IC_PARQUET, index=False)
    return len(rows)


# ───────────────────────────── serving 读取端 ─────────────────────────────

def load_health_summary() -> Optional[Dict[str, Any]]:
    """体检摘要(TopBar 卡 + /screen/health 用)。缺产物 → None(前端不显卡,诚实)。

    trend:近20日均值 vs 前40日均值(↗ +0.01 / ↘ -0.01 / →);alert:近20日均值 ≤0 或
    腰斩于前40日(且前40>0.02)。vintage 积累 ≥10 天后附真 OOS 段。
    """
    import pandas as pd
    if not MODEL_HEALTH_PARQUET.exists():
        return None
    try:
        df = pd.read_parquet(MODEL_HEALTH_PARQUET).sort_values("date")
        ics = df["ic"].astype(float)
        if len(ics) < 10:
            return None
        recent = ics.tail(20)
        prior = ics.iloc[:-20].tail(40)
        r, p = float(recent.mean()), (float(prior.mean()) if len(prior) else None)
        trend = "→"
        if p is not None:
            trend = "↗" if (r - p) > 0.01 else ("↘" if (r - p) < -0.01 else "→")
        alert = bool(r <= 0 or (p is not None and p > 0.02 and r < 0.5 * p))
        out: Dict[str, Any] = {
            "ic_mean": round(float(ics.mean()), 4), "recent20": round(r, 4),
            "prior40": (round(p, 4) if p is not None else None),
            "n_days": int(len(ics)), "trend": trend, "alert": alert,
            "asof": str(df["asof"].iloc[-1]), "note": str(df["note"].iloc[-1]),
            "series": [[str(d), round(float(v), 4)] for d, v in zip(df["date"].tail(60), ics.tail(60))],
        }
        if VINTAGE_IC_PARQUET.exists():
            v = pd.read_parquet(VINTAGE_IC_PARQUET)
            if len(v) >= 1:
                out["vintage"] = {"n_days": int(len(v)),
                                  "ic_mean": round(float(v["ic"].mean()), 4),
                                  "ready": bool(len(v) >= 10),
                                  "note": "真OOS:快照分数冻结于预测时点,无回看偏差(≥10天后作主口径)"}
        return out
    except Exception:  # noqa: BLE001
        return None
