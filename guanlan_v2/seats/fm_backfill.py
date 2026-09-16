# -*- coding: utf-8 -*-
"""③b·phase2 离线批算:FM/combo 历史真因子 → 缓存 parquet(guanlan 自有)。

**离线工具**,只在安装了 GPU、qlib 和 FM 源码依赖的环境手动跑::

    python -m guanlan_v2.seats.fm_backfill 2026-03-10 2026-04-20 --stocks-root /path/to/stocks --m 64

对每个历史交易日 D:跑引擎 FM(walk-forward W11 ckpt,PIT、只读 ≤D)+ rev20 combo,
跨簇拼接后做**当日全市场横截面 rank** → ``fm_pct`` / ``combo_pct``,写入缓存 parquet。
单次 ``model.sample`` 同时出 fm 与 combo(省一半 GPU 时间,复用 ``predict.py`` 内部件)。

落点(guanlan 自有、**不写 stocks 源码仓**):默认是本检出的
``var/seats_fm_backfill.parquet``，可用 ``--cache`` 覆盖。
列:``date``(YYYY-MM-DD)/ ``code``(SZ######)/ ``fm_cluster``(int 1..6)/ ``fm_score`` /
``fm_pct``(0-100)/ ``combo_pct``(0-100)。追加去重(按 date+code,keep=last)。

**live 服务不 import 本文件**;``/seats/factors`` 的 model 旁路只读上面那个 parquet。
PIT:predict 锚日 ≤D、ckpt 固定 W11(train ≤2026-04-15)→ ``D≤2026-04-15`` 含模型
look-ahead(调用方诚实标 ⚠)。
**Windows spawn 坑**:必须 ``if __name__ == '__main__'`` 守卫 + 用文件跑(别 ``python -`` stdin,
子进程 re-run ``<stdin>`` 会报 OSError)。
"""
from __future__ import annotations

import argparse
import gc
import os
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = REPO_ROOT / "var" / "seats_fm_backfill.parquet"


def _init(stocks_root: Path) -> None:
    """切到显式配置的 FM 源码根并初始化 qlib。"""
    stocks_root = stocks_root.expanduser().resolve()
    if not stocks_root.is_dir():
        raise FileNotFoundError(f"stocks root does not exist: {stocks_root}")
    if not (stocks_root / "config.py").is_file():
        raise FileNotFoundError(
            f"stocks root is missing config.py required by FM backfill: {stocks_root}"
        )
    os.chdir(stocks_root)
    root_text = str(stocks_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    os.environ["NO_PROXY"] = "*"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import qlib
    from config import PROVIDER_URI_MAP
    qlib.init(provider_uri=PROVIDER_URI_MAP, region="cn")


def _fm_combo_cluster(cid: int, date: str, m_samples: int, device: str):
    """单簇:一次 model.sample 同出 fm_series 与 combo(fm rank + rev20 rank)。"""
    import numpy as np
    import pandas as pd
    import torch
    from src.model.flow.predict import (
        DEFAULT_WINDOW, _build_dataset_for_date, _build_single_anchor_batch,
        _load_model, _resolve_anchor_t, _rev20_scores,
    )
    ds = _build_dataset_for_date(cid, date)
    t = _resolve_anchor_t(ds, date)                 # 锚日 ≤D(无足够 valid 则回溯,仍 ≤D)
    model = _load_model(cid, DEFAULT_WINDOW, device)
    batch = _build_single_anchor_batch(ds, t, device)
    with torch.no_grad():
        x_samp = model.sample(batch, m_samples=m_samples, steps=20, guidance_scale=2.0)
    fm_cum = x_samp.cpu().numpy().mean(axis=0).sum(axis=1)   # (N,) 未来5日累计 log ret 预测
    valid = batch["mask"].cpu().numpy()[0]
    insts = [ds.instruments[i] for i in np.arange(len(ds.instruments))[valid]]
    fm_series = pd.Series(fm_cum[valid], index=insts)
    rv = _rev20_scores(ds, t)                       # rev20 纯 past 因子(≤D)
    common = fm_series.index.intersection(rv.index)
    combo = 0.5 * fm_series.loc[common].rank() + 0.5 * rv.loc[common].rank()
    del model, ds
    gc.collect()
    if device == "cuda":
        torch.cuda.empty_cache()
    return fm_series, combo


def backfill_date(date: str, m_samples: int, device: str):
    """一个 D:6 簇拼接 → 当日横截面 rank → rows DataFrame。"""
    import pandas as pd
    from src.model.flow.predict import VALID_CLUSTERS
    fm_parts, combo_parts, clu = [], [], {}
    for cid in VALID_CLUSTERS:
        try:
            fm_s, combo_s = _fm_combo_cluster(cid, date, m_samples, device)
            fm_parts.append(fm_s)
            combo_parts.append(combo_s)
            for code in fm_s.index:
                clu[code] = cid
            print("  [c%d] N=%d" % (cid, len(fm_s)), flush=True)
        except Exception as e:  # noqa: BLE001 — 单簇失败只跳过、不毁整日
            print("  [c%d] skip: %s" % (cid, e), flush=True)
    if not fm_parts:
        return pd.DataFrame()
    fm_all = pd.concat(fm_parts)
    combo_all = pd.concat(combo_parts) if combo_parts else pd.Series(dtype=float)
    fm_pct = fm_all.rank(pct=True) * 100.0          # 当日全市场横截面分位
    combo_pct = combo_all.rank(pct=True) * 100.0
    rows = []
    for code in fm_all.index:
        rows.append({
            "date": date, "code": str(code), "fm_cluster": int(clu.get(code, 0)),
            "fm_score": float(fm_all[code]), "fm_pct": float(fm_pct[code]),
            "combo_pct": (float(combo_pct[code]) if code in combo_pct.index else None),
        })
    return pd.DataFrame(rows)


def _write_cache(df_new, cache_path: Path) -> int:
    """追加去重写缓存 parquet(按 date+code,keep=last)。"""
    import pandas as pd
    cache_path = cache_path.expanduser().resolve()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        try:
            old = pd.read_parquet(cache_path)
            df = pd.concat([old, df_new], ignore_index=True)
            df = df.drop_duplicates(subset=["date", "code"], keep="last")
        except Exception:  # noqa: BLE001 — 旧缓存坏就整盘重写
            df = df_new
    else:
        df = df_new
    df.to_parquet(cache_path, index=False)
    return len(df)


def main() -> None:
    ap = argparse.ArgumentParser(description="FM/combo 历史真因子离线批算 → 缓存 parquet")
    ap.add_argument("dates", nargs="+", help="历史交易日 YYYY-MM-DD(可多个)")
    ap.add_argument("--m", type=int, default=64, help="m_samples(默认 64 快;生产 256 稳)")
    ap.add_argument(
        "--stocks-root", type=Path,
        default=(Path(os.environ["GUANLAN_STOCKS_ROOT"])
                 if os.environ.get("GUANLAN_STOCKS_ROOT") else None),
        help="FM/qlib 源码根；也可设置 GUANLAN_STOCKS_ROOT",
    )
    ap.add_argument(
        "--cache", type=Path,
        default=Path(os.environ.get("GUANLAN_FM_CACHE", str(DEFAULT_CACHE_PATH))),
        help="输出 parquet（默认当前观澜检出的 var/）",
    )
    args = ap.parse_args()

    if args.stocks_root is None:
        ap.error("--stocks-root is required (or set GUANLAN_STOCKS_ROOT)")
    _init(args.stocks_root)
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("DEVICE %s  M %d  DATES %s" % (device, args.m, ",".join(args.dates)), flush=True)
    for date in args.dates:
        t0 = time.time()
        df = backfill_date(date, args.m, device)
        if df.empty:
            print("DATE %s -> EMPTY (no clusters resolved)" % date, flush=True)
            continue
        total = _write_cache(df, args.cache)
        print("DATE %s -> rows=%d sec=%.1f cache_total=%d" % (
            date, len(df), time.time() - t0, total), flush=True)
    print("BACKFILL_DONE cache=%s" % args.cache.expanduser().resolve(), flush=True)


if __name__ == "__main__":
    import multiprocessing as mp
    mp.freeze_support()
    main()
