# -*- coding: utf-8 -*-
"""篮子前向持有收益(P1 §2)纯函数:闭环第 3 环「D 日选的股后来怎么样」的计算件。

口径对齐置信校准(seats/calibration.py):start(或其后首根)收盘进 → +horizon 根收盘出,
等权,不含成本;出场 bar 未到 → matured:false,ret 给到最新可算段(entry→最新收盘,
绝不冒充已实现)。基准 = 全A等权同窗累计(eqw_market 产物;缺失/不覆盖 → None 显形)。
纯函数零 IO:closes_by_code 与 bench_df 由调用方注入(端点层负责拉日线/读产物)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from guanlan_v2.strategy.compute.eqw_market import eqw_cum_ret

NOTE = ("口径:start(或其后首根)收盘进、+N根收盘出,等权,不含成本;"
        "基准=全A等权同窗累计;未成熟给到最新段并标 matured:false")


def compute_basket_perf(closes_by_code: Dict[str, Sequence[Tuple[str, float]]],
                        start: str, horizon: int, bench_df=None) -> Dict[str, Any]:
    """closes_by_code: {code: [(date 'YYYY-MM-DD', close), ...] 升序}。返回响应形 dict。"""
    hz = max(1, min(int(horizon or 5), 60))
    per: List[Dict[str, Any]] = []
    warnings: List[str] = []
    bench_vals: List[float] = []
    for code, series in (closes_by_code or {}).items():
        rows = [(str(d), float(v)) for d, v in (series or []) if v is not None and v == v]
        idx = next((i for i, (d, _) in enumerate(rows) if d >= str(start)), None)
        if idx is None:
            warnings.append(f"{code}: start 之后无可用bar,剔除")
            continue
        entry_date, entry = rows[idx]
        if entry <= 0:
            warnings.append(f"{code}: 入场价非正,剔除")
            continue
        matured = (idx + hz) < len(rows)
        exit_i = (idx + hz) if matured else (len(rows) - 1)
        if exit_i <= idx:
            warnings.append(f"{code}: 入场后无后续bar(未成熟且无可算段),剔除")
            continue
        exit_date, exitp = rows[exit_i]
        per.append({"code": code, "entry_date": entry_date, "entry": entry,
                    "exit_date": exit_date, "exit": exitp,
                    "ret": exitp / entry - 1.0, "matured": matured})
        b = eqw_cum_ret(bench_df, entry_date, exit_date)
        if b is not None:
            bench_vals.append(b)
    if not per:
        return {"ok": False, "reason": "无任何可算票", "warnings": warnings}
    avg = sum(p["ret"] for p in per) / len(per)
    bench: Optional[float] = None
    if bench_vals and len(bench_vals) == len(per):
        bench = sum(bench_vals) / len(bench_vals)
    elif bench_vals:                                   # 部分覆盖=口径不齐 → 整体 null 显形
        warnings.append("基准窗口未全覆盖,bench_ret 置 null(诚实缺席)")
    return {"ok": True, "n": len(per), "matured_n": sum(1 for p in per if p["matured"]),
            "horizon": hz, "avg_ret": avg, "bench_ret": bench,
            "excess": (avg - bench) if bench is not None else None,
            "per_code": per, "warnings": warnings, "note": NOTE}
