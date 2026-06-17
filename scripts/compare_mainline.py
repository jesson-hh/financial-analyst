# -*- coding: utf-8 -*-
"""迁移校验:引擎原生主线面板 vs qlib monthly_mainlines_panel,逐 (日期×行业) 对比。

跑法(引擎 venv / py3.13,无 qlib):
    G:/financial-analyst/.venv/Scripts/python.exe scripts/compare_mainline.py

引擎原生  guanlan_v2.strategy.compute.mainline.build_mainline(provider_uri)
qlib 基线  G:/stocks/strategy/mainline/monthly_mainlines_panel.parquet

**status** 是产品真正消费的列(L2 主线),要求精确匹配率 100%。只读,不落盘。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guanlan_v2.strategy.compute.mainline import build_mainline  # noqa: E402

PROVIDER_URI = "G:/stocks/stock_data/cn_data"
QLIB_MONTHLY = "G:/stocks/strategy/mainline/monthly_mainlines_panel.parquet"
START, END = "2023-08-01", "2026-06-05"

KEY = ["datetime", "industry"]
# 整数列(计数/排名/tier)要求逐位 0;status 精确匹配率。
EXACT0 = {
    "lu_count", "up_count", "down_count", "stock_count", "is_top10",
    "lu_mv_lt50", "lu_mv_50_100", "lu_mv_100_200", "lu_mv_200_500", "lu_mv_ge500",
    "lu_count_ge200_yi", "lu_count_ge500_yi",
    "lu_rank_today", "ret_rank_today", "amt_rank_today",
}
# 其余数值列:max_abs<=ABS 或 max_rel<=REL 即 PASS(穿零列靠 ABS)。
ABS, REL = 1e-4, 1e-5
# total_amount 是大额求和(float32 噪声)→ 放宽相对。
REL_OVERRIDE = {"total_amount": 1e-6, "total_amount_yi": 1e-6}


def main() -> int:
    print(f"== 引擎原生主线构建 ({START}→{END}) ...", flush=True)
    e = build_mainline(PROVIDER_URI, START, END)
    e["datetime"] = pd.to_datetime(e["datetime"])
    print(f"   engine: {e.shape}  {str(e['datetime'].min())[:10]}→{str(e['datetime'].max())[:10]}  行业 {e['industry'].nunique()}")

    q = pd.read_parquet(QLIB_MONTHLY)
    q["datetime"] = pd.to_datetime(q["datetime"])
    print(f"== qlib: {q.shape}")

    # 对齐 (日期×行业)
    em = e.set_index(KEY).sort_index()
    qm = q.set_index(KEY).sort_index()
    common = em.index.intersection(qm.index)
    only_e = len(em.index.difference(qm.index))
    only_q = len(qm.index.difference(em.index))
    print(f"\n=== 键对齐: common {len(common)}  only_engine {only_e}  only_qlib {only_q} ===")
    em, qm = em.loc[common], qm.loc[common]

    rate = 1.0
    # status 头条
    if "status" in em.columns and "status" in qm.columns:
        match = (em["status"].astype(str).values == qm["status"].astype(str).values)
        rate = float(match.mean())
        print(f"\n*** status 精确匹配率: {rate:.4%}  ({int(match.sum())}/{len(match)}) ***")
        if rate < 1.0:
            mism = em.index[~match][:10]
            for k in mism:
                print(f"    mismatch {k}: engine={em.loc[k,'status']} qlib={qm.loc[k,'status']}")

    # 数值列逐列
    num_cols = [c for c in qm.columns if c != "status" and pd.api.types.is_numeric_dtype(qm[c])]
    print(f"\n=== 数值列 ({len(num_cols)}) ===")
    all_ok = (rate == 1.0)
    for col in sorted(num_cols):
        if col not in em.columns:
            print(f"  [skip] {col:<22} engine 缺列"); all_ok = False; continue
        x = pd.to_numeric(em[col], errors="coerce")
        y = pd.to_numeric(qm[col], errors="coerce")
        both = x.notna() & y.notna()
        only = int((x.notna() ^ y.notna()).sum())
        diff = (x[both] - y[both]).abs()
        mx = float(diff.max()) if len(diff) else 0.0
        denom = y[both].abs().replace(0.0, np.nan)
        mxrel = float((diff / denom).max()) if len(denom.dropna()) else 0.0
        if col in EXACT0:
            ok = (mx == 0.0) and (only == 0)
            info = f"max|Δ|={mx:.3e} (exact)"
        else:
            rel = REL_OVERRIDE.get(col, REL)
            ok = ((mx <= ABS) or (mxrel <= rel)) and (only == 0)
            info = f"max|Δ|={mx:.3e} max rel={mxrel:.2e}"
        all_ok = all_ok and ok
        tag = "PASS" if ok else "**FAIL**"
        nan_tag = f" nan_mis={only}" if only else ""
        worst = ""
        if not ok and len(diff):
            d = diff.idxmax()
            worst = f"  worst@{str(d[0])[:10]}/{d[1]} e={x.loc[d]:.6g} q={y.loc[d]:.6g}"
        print(f"  [{tag}] {col:<22} {info}{nan_tag}{worst}")

    # 末日 06-05 状态分布
    last = em.index.get_level_values("datetime").max()
    le = em.xs(last, level="datetime")["status"].value_counts().to_dict()
    lq = qm.xs(last, level="datetime")["status"].value_counts().to_dict()
    print(f"\n=== 末日 {str(last)[:10]} status 分布 ===\n  engine={le}\n  qlib  ={lq}")

    print("\n" + "=" * 60)
    print(f"  mainline 迁移校验: {'[OK] 全 PASS — 迁移等价' if all_ok else '[X] 有 FAIL — 见上'}")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
