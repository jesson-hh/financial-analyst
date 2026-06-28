# -*- coding: utf-8 -*-
"""GAT 关系图模型每日训练 + 推理(guanlan 自有·conda stocks GPU)。

跑法:
    D:/app/miniconda/envs/stocks/python.exe scripts/gat_predict.py --date 2026-06-27

读 close/volume(QlibBinaryLoader 直读二进制)→ gat_io 每日 (X,A,y) → gat_model 训练
→ eval_date 前向 → pred_ret_5d → 写 var/dl_pred_gat.parquet(DL 集成层契约;train_cutoff 诚实落盘)。
**命门**:GPU 训练/推理离线;9999 请求路径绝不跑模型。
"""
import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "engine"))

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader   # noqa: E402
from guanlan_v2.strategy.compute.breadth import list_all_instruments       # noqa: E402
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date  # noqa: E402
from guanlan_v2.strategy.compute.gat_io import (                            # noqa: E402
    compute_node_features, build_corr_graph, build_corr_neighbors, forward_label, rebalance_dates)
from guanlan_v2.strategy.compute.gat_model import (                         # noqa: E402
    train_gat, predict_gat, train_gat_sparse, predict_gat_sparse)
from guanlan_v2.strategy.compute.fincast_io import write_pred_rolling       # noqa: E402

HORIZON = 5
OUT = str(_REPO / "var" / "dl_pred_gat.parquet")


def _read_panels(loader, codes, eval_date):
    """逐码读 close/volume bins → (close_panel, volume_panel),截到 ≤ eval_date(不看未来)。"""
    close, vol = {}, {}
    for c in codes:
        try:
            s = loader._read_bin(c, "close")
            if s is not None and len(s):
                close[c] = s
            v = loader._read_bin(c, "volume")
            if v is not None and len(v):
                vol[c] = v
        except Exception:   # noqa: BLE001 — 单码读失败跳过
            continue
    if not close:
        raise RuntimeError("无任何可读 close(检查 provider_uri)")
    cp = pd.DataFrame(close).sort_index(); cp.index = pd.DatetimeIndex(cp.index)
    cp = cp.loc[:pd.Timestamp(eval_date)]
    vp = None
    if vol:
        vp = pd.DataFrame(vol).sort_index(); vp.index = pd.DatetimeIndex(vp.index)
        vp = vp.loc[:pd.Timestamp(eval_date)]
    return cp, vp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="评估日 YYYY-MM-DD(缺省=最新交易日)")
    ap.add_argument("--device", default="cuda", help="cuda|cpu(无卡自动退 cpu)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--train-start", default="2022-01-01")
    ap.add_argument("--window", type=int, default=60)
    ap.add_argument("--topk", type=int, default=20)
    ap.add_argument("--universe", default="all",
                    help="all=全市场(默认);csi300/csi500/csi800/csi1000=指数成分(节点少·稠密图省显存,适合首跑打通)")
    ap.add_argument("--graph", default="auto", choices=("auto", "dense", "sparse"),
                    help="auto=全市场(all)走稀疏 kNN 图·否则稠密(默认);dense=稠密 N×N(小盘);sparse=稀疏 kNN(全市场不爆显存)")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    args = ap.parse_args()

    import torch
    device = args.device if (args.device != "cuda" or torch.cuda.is_available()) else "cpu"
    eval_date = args.date or _latest_trade_date(args.provider)
    print(f"评估日 {eval_date} · device {device} · provider {args.provider}", flush=True)

    loader = QlibBinaryLoader(args.provider)
    if args.universe in ("all", "", None):
        codes = list_all_instruments(args.provider)
    else:
        from financial_analyst.data.universe import resolve_universe_codes
        codes = [str(c) for c in resolve_universe_codes(args.universe)]
    print(f"universe={args.universe} · {len(codes)} 码,读 close/volume 面板 ...", flush=True)
    # 图模式:auto=全市场(all)走稀疏 kNN(O(N·K) 不爆显存),否则稠密 N×N;dense/sparse 显式覆盖。
    use_sparse = (args.universe == "all") if args.graph == "auto" else (args.graph == "sparse")
    print(f"graph={'sparse' if use_sparse else 'dense'}(--graph={args.graph})", flush=True)
    cp, vp = _read_panels(loader, codes, eval_date)

    rdates = [d for d in rebalance_dates(cp.index, horizon=HORIZON, start=args.train_start)
              if d < pd.Timestamp(eval_date)]
    X_list, A_list, y_list, cutoff = [], [], [], None
    t0 = time.time()
    for d in rdates:
        node_codes, X = compute_node_features(cp, vp, d)
        if len(node_codes) < 50:
            continue
        y = forward_label(cp, d, node_codes, horizon=HORIZON)
        if int(np.isfinite(y).sum()) < 50:
            continue
        if use_sparse:
            A = build_corr_neighbors(cp, d, node_codes, window=args.window, topk=args.topk)
        else:
            A = build_corr_graph(cp, d, node_codes, window=args.window, topk=args.topk)
        X_list.append(X); A_list.append(A); y_list.append(y); cutoff = d
    if len(X_list) < 10:
        print(f"训练样本不足({len(X_list)} 日),退出(不产文件 → 下游诚实退纯 LGB)", flush=True)
        return
    print(f"训练日 {len(X_list)} · 末标签日 {cutoff.date()} · 训练 GAT(epochs={args.epochs}) ...", flush=True)
    if use_sparse:
        model = train_gat_sparse(X_list, A_list, y_list, device=device, epochs=args.epochs)
    else:
        model = train_gat(X_list, A_list, y_list, device=device, epochs=args.epochs)

    e_codes, Xe = compute_node_features(cp, vp, eval_date)
    if use_sparse:
        Ae = build_corr_neighbors(cp, eval_date, e_codes, window=args.window, topk=args.topk)
        preds = predict_gat_sparse(model, Xe, Ae, device=device)
    else:
        Ae = build_corr_graph(cp, eval_date, e_codes, window=args.window, topk=args.topk)
        preds = predict_gat(model, Xe, Ae, device=device)
    out = write_pred_rolling(OUT, eval_date, e_codes, np.asarray(preds, dtype=np.float32),
                             keep_days=60, train_cutoff=str(cutoff.date()))
    print(f"已写 {OUT}({len(out)} 条 · {pd.to_datetime(out['eval_date']).nunique()} 日 · {time.time() - t0:.1f}s)",
          flush=True)


if __name__ == "__main__":
    main()
