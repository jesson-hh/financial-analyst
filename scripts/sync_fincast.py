# -*- coding: utf-8 -*-
"""#7 FinCast 桥接(离线·无 GPU)：把 qlib 侧 FinCast 预测 → guanlan 自有 parquet。

生产端 `G:/stocks/tsfm_exp/scripts/fincast_daily_predict.py`(零样本 FinCast v1·GPU·conda
`stocks` 环境)把每日预测写到 `G:/stocks/stock_data/parquet/fincast_daily_pred.parquet`
(index=(instrument, eval_date),col=pred_ret_5d,代码 SH######,**零样本→无训练窗 look-ahead**,
PIT 只用 ≤eval_date 的 close)。本脚本把它**搬成 guanlan 自有的扁平列 parquet**
`var/v4_fincast_pred.parquet`(eval_date/instrument/pred_ret_5d),让 `compute/v4.py:build_v4`
的 B3 集成只读 guanlan 侧、与 qlib 解耦(regen 不碰 G:/stocks,同 fm_backfill 的「自有落点」范式)。

**只搬数据、绝不跑模型**(GPU 推理是生产端 fincast_daily_predict.py 的事)。要刷新预测到当前交易日:
    D:/app/miniconda/envs/stocks/python.exe G:/stocks/tsfm_exp/scripts/fincast_daily_predict.py
然后再跑本脚本同步过来。

用法:  python scripts/sync_fincast.py  [--src <qlib parquet>] [--out <guanlan parquet>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

DEFAULT_SRC = "G:/stocks/stock_data/parquet/fincast_daily_pred.parquet"
DEFAULT_OUT = str(Path(__file__).resolve().parents[1] / "var" / "v4_fincast_pred.parquet")


def sync(src: str = DEFAULT_SRC, out: str = DEFAULT_OUT) -> dict:
    """读 qlib FinCast parquet → 规整成扁平列 → 写 guanlan var/。返回摘要 dict。"""
    sp = Path(src)
    if not sp.exists():
        raise FileNotFoundError(
            f"qlib FinCast 预测不存在: {src}\n  先在 conda stocks 环境跑生产端 "
            f"fincast_daily_predict.py 产出预测。")
    df = pd.read_parquet(sp)
    # MultiIndex (instrument, eval_date)/pred_ret_5d → 扁平列(契约:eval_date/instrument/pred_ret_5d)
    flat = df.reset_index()
    cols = {c.lower(): c for c in flat.columns}
    need = ("instrument", "eval_date", "pred_ret_5d")
    missing = [c for c in need if c not in cols]
    if missing:
        raise ValueError(f"源 parquet 缺列 {missing};实有 {list(flat.columns)}")
    flat = flat.rename(columns={cols["instrument"]: "instrument",
                                cols["eval_date"]: "eval_date",
                                cols["pred_ret_5d"]: "pred_ret_5d"})
    flat["eval_date"] = pd.to_datetime(flat["eval_date"]).dt.strftime("%Y-%m-%d")
    flat = flat[["eval_date", "instrument", "pred_ret_5d"]].dropna(subset=["pred_ret_5d"])
    flat["instrument"] = flat["instrument"].astype(str)

    op = Path(out)
    op.parent.mkdir(parents=True, exist_ok=True)
    flat.to_parquet(op, index=False)
    dates = sorted(flat["eval_date"].unique())
    return {"rows": len(flat), "n_dates": len(dates),
            "date_min": dates[0] if dates else None, "date_max": dates[-1] if dates else None,
            "out": str(op)}


def main() -> int:
    ap = argparse.ArgumentParser(description="FinCast qlib→guanlan parquet 桥接(无 GPU)")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    info = sync(args.src, args.out)
    print(f"SYNCED rows={info['rows']} dates={info['n_dates']} "
          f"({info['date_min']}~{info['date_max']}) -> {info['out']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
