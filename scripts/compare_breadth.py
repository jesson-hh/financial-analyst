# -*- coding: utf-8 -*-
"""迁移校验:引擎原生宽度计算 vs qlib 产物,逐日逐列对比。

跑法(引擎 venv / py3.13,无 qlib):
    G:/financial-analyst/.venv/Scripts/python.exe scripts/compare_breadth.py

对比对象:
    引擎原生  guanlan_v2.strategy.compute.breadth.build_breadth(provider_uri)
    qlib 基线  G:/stocks/strategy/research/market_breadth_panel.parquet
              G:/stocks/strategy/research/market_breadth_resid.parquet

二者**读同一份已修复的 .bin**,故应逐位吻合;此脚本量化残差并给 PASS/FAIL。
只读,不落盘。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:  # 控制台 gbk 时也能打中文/符号
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 让 `python scripts/xx.py` 能 import 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from guanlan_v2.strategy.compute.breadth import build_breadth  # noqa: E402

PROVIDER_URI = "G:/stocks/stock_data/cn_data"
QLIB_PANEL = "G:/stocks/strategy/research/market_breadth_panel.parquet"
QLIB_RESID = "G:/stocks/strategy/research/market_breadth_resid.parquet"
START, END = "2019-11-01", "2026-06-05"

# 绝对容差:计数/比率/分位要求逐位相等(0 或 1e-9 浮点噪声)。
TOL = {
    "stock_count": 0, "up_count": 0, "down_count": 0, "flat_count": 0,
    "limit_up_10": 0, "limit_up_20": 0, "limit_down_10": 0, "limit_up_total": 0,
    "total_amount_yi": 1e-9,
    "mean_ret": 1e-9, "median_ret": 1e-9, "up_ratio": 1e-9,
    "amount_pct_60d": 1e-9, "upratio_pct_60d": 1e-9, "lu_count_pct_60d": 1e-9,
    "amount_pct_250d": 1e-9, "upratio_pct_250d": 1e-9, "lu_count_pct_250d": 1e-9,
    "lu_resid_pct60": 1e-9, "amt_resid_pct60": 1e-9,
    # 残差列会穿越 0,相对误差在零点附近发散 → 用绝对容差。其量级远小于列本身:
    #   lu_resid 单位=涨停家数(典型几十~上百),amt_resid 单位=亿元(典型几百~几千),
    #   实测 max|Δ| 仅 1.2e-4 / 5.5e-3,纯属 total_amount float32 求和噪声经回归放大。
    "lu_resid": 1e-2, "amt_resid": 1e-1,
}
DEFAULT_TOL = 1e-9

# 相对容差:仅用于「求和/回归值」列 —— qlib 把 total_amount 存成 float32,引擎按
# float64 求和(更准),二者在 float32 精度内一致(~5e-8 相对);amt_resid 是
# total_amount_yi 的残差,继承该噪声。这些**不影响任何下游分位/信号**(分位列已逐位等价)。
RELTOL = {
    "total_amount": 1e-6,
    "total_amount_yi": 1e-6,
}


def _cmp(label: str, a: pd.DataFrame, b: pd.DataFrame, cols) -> bool:
    """对齐公共日期 × 列,打印 max|Δ| 与最差日;返回是否全 PASS。"""
    common = a.index.intersection(b.index)
    print(f"\n=== {label}: 公共日期 {len(common)} "
          f"({str(common.min())[:10]} → {str(common.max())[:10]}) ===")
    a2, b2 = a.loc[common], b.loc[common]
    all_ok = True
    for col in cols:
        if col not in a2.columns or col not in b2.columns:
            print(f"  [skip] {col:<20} 缺列 (engine={col in a2.columns} qlib={col in b2.columns})")
            continue
        x = pd.to_numeric(a2[col], errors="coerce")
        y = pd.to_numeric(b2[col], errors="coerce")
        both = x.notna() & y.notna()
        only = int((x.notna() ^ y.notna()).sum())   # 一边 NaN 一边非 NaN
        diff = (x[both] - y[both]).abs()
        mx = float(diff.max()) if len(diff) else 0.0
        if col in RELTOL:
            denom = y[both].abs().replace(0.0, np.nan)
            rel = (diff / denom)
            mxrel = float(rel.max()) if len(rel.dropna()) else 0.0
            reltol = RELTOL[col]
            ok = (mxrel <= reltol) and (only == 0)
            tag = "PASS" if ok else "**FAIL**"
            worst = ""
            if mxrel > reltol:
                d = rel.idxmax()
                worst = f"  worst@{str(d)[:10]} engine={x.loc[d]:.6g} qlib={y.loc[d]:.6g}"
            nan_tag = f"  nan_mismatch={only}" if only else ""
            print(f"  [{tag}] {col:<20} max|Δ|={mx:.3e} max rel={mxrel:.2e} reltol={reltol:.0e}{nan_tag}{worst}")
        else:
            tol = TOL.get(col, DEFAULT_TOL)
            ok = (mx <= tol) and (only == 0)
            tag = "PASS" if ok else "**FAIL**"
            worst = ""
            if len(diff) and mx > tol:
                d = diff.idxmax()
                worst = f"  worst@{str(d)[:10]} engine={x.loc[d]:.6g} qlib={y.loc[d]:.6g}"
            nan_tag = f"  nan_mismatch={only}" if only else ""
            print(f"  [{tag}] {col:<20} max|Δ|={mx:.3e} tol={tol:.0e}{nan_tag}{worst}")
        all_ok = all_ok and ok
    return all_ok


def main() -> int:
    print(f"== 引擎原生构建 (provider={PROVIDER_URI}, {START}→{END}) ...", flush=True)
    e_panel, e_resid = build_breadth(PROVIDER_URI, START, END)
    print(f"   engine panel {e_panel.shape} {str(e_panel.index.min())[:10]}→{str(e_panel.index.max())[:10]}")
    print(f"   engine resid {e_resid.shape}")

    q_panel = pd.read_parquet(QLIB_PANEL)
    q_resid = pd.read_parquet(QLIB_RESID)
    print(f"== qlib panel {q_panel.shape} | qlib resid {q_resid.shape}")

    panel_cols = [
        "stock_count", "up_count", "down_count", "flat_count",
        "limit_up_10", "limit_up_20", "limit_down_10", "limit_up_total",
        "total_amount", "total_amount_yi", "mean_ret", "median_ret", "up_ratio",
        "amount_pct_60d", "upratio_pct_60d", "lu_count_pct_60d",
        "amount_pct_250d", "upratio_pct_250d", "lu_count_pct_250d",
    ]
    resid_cols = ["lu_resid", "amt_resid", "lu_resid_pct60", "amt_resid_pct60"]

    ok_panel = _cmp("PANEL", e_panel, q_panel, panel_cols)
    ok_resid = _cmp("RESID", e_resid, q_resid, resid_cols)

    # 末日横截面抽样(肉眼核对 06-05)
    last = e_panel.index.max()
    if last in q_panel.index:
        print(f"\n=== 末日 {str(last)[:10]} 抽样 ===")
        for col in ["total_amount_yi", "limit_up_total", "up_ratio", "lu_count_pct_60d"]:
            print(f"  {col:<18} engine={e_panel.loc[last, col]:.4f}  qlib={q_panel.loc[last, col]:.4f}")
        for col in resid_cols:
            print(f"  {col:<18} engine={e_resid.loc[last, col]:.4f}  qlib={q_resid.loc[last, col]:.4f}")

    print("\n" + "=" * 60)
    verdict = "[OK] 全 PASS — 迁移逐位等价" if (ok_panel and ok_resid) else "[X] 有 FAIL — 见上"
    print(f"  breadth 迁移校验: {verdict}")
    print("=" * 60)
    return 0 if (ok_panel and ok_resid) else 1


if __name__ == "__main__":
    raise SystemExit(main())
