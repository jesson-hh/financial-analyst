# -*- coding: utf-8 -*-
"""LSTM 生产 DL 源:全市场 38 因子 PIT 面板 → 序列训练(torch CPU) → 截面预测 5 日收益
→ 直写 var/dl_pred_lstm.parquet(带 train_cutoff)+ 存 model.pt。

guanlan 主 env 跑(torch 2.10+cpu);也被 lstm_workflow(发布端点)import。
    python -m guanlan_v2.strategy.compute.lstm_predict --date 2026-06-22 --universe csi800
**命门**:训练/推理离线;9999 请求路径绝不跑。PIT:train_cutoff = eval_date − horizon。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
from financial_analyst.data.universe import resolve_universe_codes
from guanlan_v2.strategy.compute.breadth import list_all_instruments
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date
from guanlan_v2.strategy.compute.v4 import build_feature_panel, _select_mf
from guanlan_v2.strategy.compute.lstm_io import (
    add_forward_return, build_sequences, predict_index,
)
from guanlan_v2.strategy.compute.fincast_io import write_pred_rolling

_REPO = Path(__file__).resolve().parents[3]
OUT = str(_REPO / "var" / "dl_pred_lstm.parquet")
MODEL_PT = str(_REPO / "var" / "models" / "lstm" / "latest.pt")
LABEL_COL = "__fwd_ret__"


def _train_lstm(X: np.ndarray, y: np.ndarray, n_features: int, hidden: int,
                layers: int, lr: float, epochs: int):
    """镜像 _lstm_eval 的 torch 训练(CPU·seed 固定·nn.LSTM→Linear·Adam/MSE)。返回 net。"""
    import torch
    from torch import nn
    torch.manual_seed(0)
    torch.set_num_threads(4)
    Xtr = torch.tensor(X)                       # (N,L,F) float32
    ytr = torch.tensor(y).view(-1, 1)

    class _LSTMNet(nn.Module):
        def __init__(self, nf, hid, nl):
            super().__init__()
            self.lstm = nn.LSTM(nf, hid, nl, batch_first=True)
            self.head = nn.Linear(hid, 1)

        def forward(self, x):
            out, _h = self.lstm(x)
            return self.head(out[:, -1, :])

    net = _LSTMNet(n_features, hidden, layers)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    lossfn = nn.MSELoss()
    net.train()
    bs, N = 256, int(Xtr.shape[0])
    for _ep in range(epochs):
        perm = torch.randperm(N)
        for b in range(0, N, bs):
            sel = perm[b:b + bs]
            opt.zero_grad()
            loss = lossfn(net(Xtr[sel]), ytr[sel])
            loss.backward()
            opt.step()
    net.eval()
    return net


def _predict(net, X: np.ndarray) -> np.ndarray:
    import torch
    with torch.no_grad():
        out = net(torch.tensor(X)).view(-1).cpu().numpy()
    return np.asarray(out, dtype=np.float32)


def _close_series(loader, codes, start, end) -> pd.Series:
    """逐码读 close → (instrument, datetime) 长 Series(float32·ffill)。
    build_feature_panel 把 close 化成因子不回传原始 close,前向收益标签需原始 close 单独读。"""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    frames = []
    for code in codes:
        s = loader._read_bin(code, "close")
        if s is None:
            continue
        s = s.loc[(s.index >= start_ts) & (s.index <= end_ts)]
        if s.empty:
            continue
        df = s.astype("float32").ffill().to_frame("close")
        df.index.name = "datetime"
        df["instrument"] = code
        frames.append(df.set_index("instrument", append=True))
    if not frames:
        raise RuntimeError("无任何可读 close")
    c = pd.concat(frames, axis=0).reorder_levels(["instrument", "datetime"]).sort_index()
    return c["close"]


def train_and_predict(provider: str = DEFAULT_PROVIDER, eval_date: Optional[str] = None,
                      universe: str = "csi800", seq_len: int = 10, hidden: int = 32,
                      layers: int = 1, lr: float = 1e-3, epochs: int = 40, horizon: int = 5,
                      sample_cap: int = 6000, history_days: int = 730,
                      out_path: str = OUT, model_path: str = MODEL_PT) -> dict:
    eval_date = eval_date or _latest_trade_date(provider)
    eval_ts = pd.Timestamp(eval_date)
    start = (eval_ts - pd.Timedelta(days=history_days)).date().isoformat()
    print(f"[lstm_predict] eval_date {eval_date} · universe {universe} · provider {provider}", flush=True)

    loader = QlibBinaryLoader(provider)
    pred_codes = list_all_instruments(provider)
    print(f"[lstm_predict] 全市场 {len(pred_codes)} 码,build_feature_panel ...", flush=True)
    panel = build_feature_panel(loader, pred_codes, start, eval_date)
    feat_cols = _select_mf(list(panel.columns), None)        # 38 v4 因子(在注入 close/label 前取,防泄漏)
    panel["close"] = _close_series(loader, pred_codes, start, eval_date).reindex(panel.index)
    panel = add_forward_return(panel, horizon)               # 用原始 close 算前向收益标签
    panel[feat_cols] = panel[feat_cols].fillna(0.0)          # 复刻 v4 LGB 的 fillna(0):特征窗全有限,否则序列窗含 NaN 被全剔

    dates = sorted(pd.DatetimeIndex(panel.index.get_level_values("datetime")).unique())
    if len(dates) <= horizon:
        raise RuntimeError(f"面板交易日 {len(dates)} ≤ horizon {horizon};历史太短")
    cutoff = dates[-(horizon + 1)]                            # = eval_date − horizon 交易日 < eval_date

    train_codes = set(str(c) for c in resolve_universe_codes(universe))
    tr_mask = panel.index.get_level_values("instrument").isin(train_codes)
    train_panel = panel[tr_mask]
    X, y, _idx = build_sequences(train_panel, feat_cols, LABEL_COL, seq_len, cutoff)
    if len(X) < 10:
        raise RuntimeError(f"训练序列不足 ({len(X)}<10);universe/seq_len 调整")
    if len(X) > sample_cap:                                   # 定种子下采样守 CPU 时延
        rng = np.random.RandomState(0)
        sel = rng.choice(len(X), sample_cap, replace=False)
        X, y = X[sel], y[sel]
    print(f"[lstm_predict] 训练样本 {len(X)} · 特征 {len(feat_cols)} · cutoff {cutoff.date()} · 训练 ...", flush=True)

    t0 = time.time()
    net = _train_lstm(X, y, len(feat_cols), hidden, layers, lr, epochs)
    print(f"[lstm_predict] 训练完 {time.time()-t0:.1f}s · 截面预测 ...", flush=True)

    Xp, codes = predict_index(panel, feat_cols, seq_len, eval_date)
    if not codes:
        raise RuntimeError("截面预测无有效标的(末窗不足/含非有限)")
    preds = _predict(net, Xp)
    out = write_pred_rolling(out_path, eval_date, codes, preds, keep_days=60, train_cutoff=cutoff)

    Path(model_path).parent.mkdir(parents=True, exist_ok=True)
    import torch
    torch.save(net.state_dict(), model_path)
    print(f"[lstm_predict] 已写 {out_path}({len(codes)} 只 · cutoff {cutoff.date()} · "
          f"mean {float(np.mean(preds)):+.4f})+ model.pt", flush=True)
    return {"eval_date": str(eval_ts.date()), "train_cutoff": str(cutoff.date()),
            "n_train": int(len(X)), "n_pred": int(len(codes)), "out": out_path}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="评估日 YYYY-MM-DD(缺省=最新交易日)")
    ap.add_argument("--universe", default="csi800", help="训练池(预测恒全市场)")
    ap.add_argument("--seq-len", type=int, default=10)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--layers", type=int, default=1)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--sample-cap", type=int, default=6000)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    a = ap.parse_args()
    train_and_predict(provider=a.provider, eval_date=(a.date or None), universe=a.universe,
                      seq_len=a.seq_len, hidden=a.hidden, layers=a.layers, lr=a.lr,
                      epochs=a.epochs, horizon=a.horizon, sample_cap=a.sample_cap)


if __name__ == "__main__":
    main()
