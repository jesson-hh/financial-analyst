# tests/test_lstm_io.py
# LSTM 港移纯函数门禁:前向收益 horizon 对齐;PIT 序列闸(label_date≤cutoff);截面预测末窗。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.strategy.compute.lstm_io import (  # noqa: E402
    add_forward_return, build_sequences, predict_index,
)


def _panel(codes, dates, fcols):
    """造 MultiIndex (instrument,datetime) 面板:每特征列 = 行序号(可预测),close = 100+行序号。"""
    rows = []
    idx = []
    for c in codes:
        for i, d in enumerate(dates):
            idx.append((c, pd.Timestamp(d)))
            rows.append([float(i)] * len(fcols) + [100.0 + i])
    df = pd.DataFrame(rows, columns=list(fcols) + ["close"],
                      index=pd.MultiIndex.from_tuples(idx, names=["instrument", "datetime"]))
    return df.sort_index()


def test_add_forward_return_horizon():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    p = _panel(["SH600000"], dates, ["f1"])
    out = add_forward_return(p, horizon=5)
    s = out.xs("SH600000", level="instrument")["__fwd_ret__"]
    # close = 100..109;t=0 → close[5]/close[0]-1 = 105/100-1 = 0.05
    assert abs(float(s.iloc[0]) - 0.05) < 1e-6
    # 末 horizon 行无前向收益 → NaN
    assert bool(np.isnan(s.iloc[-1]))


def test_build_sequences_shape_and_pit_gate():
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    p = _panel(["SH600000", "SZ000001"], dates, ["f1", "f2"])
    p = add_forward_return(p, horizon=5)
    cutoff = pd.Timestamp(dates[20])      # 只收 label_date ≤ dates[20]
    X, y, idx = build_sequences(p, ["f1", "f2"], "__fwd_ret__", seq_len=4, cutoff=cutoff)
    assert X.dtype == np.float32 and X.ndim == 3 and X.shape[1:] == (4, 2)
    assert len(y) == len(idx) == X.shape[0]
    # PIT 闸:无样本 label_date > cutoff
    assert all(d <= cutoff for d, _c in idx)
    # 窗口右端 = label_date;f1 = 行序号 → 末步 = label_date 的行序号
    pos = {pd.Timestamp(d): i for i, d in enumerate(dates)}
    d0, _c0 = idx[0]
    assert abs(float(X[0, -1, 0]) - float(pos[d0])) < 1e-6


def test_build_sequences_drops_unrealized_label():
    dates = pd.date_range("2026-01-01", periods=12, freq="D")
    p = _panel(["SH600000"], dates, ["f1"])
    p = add_forward_return(p, horizon=5)
    # cutoff 放最后一天:末 horizon 行 label NaN → 必被剔(不入训)
    X, y, idx = build_sequences(p, ["f1"], "__fwd_ret__", seq_len=3, cutoff=pd.Timestamp(dates[-1]))
    assert all(d <= pd.Timestamp(dates[-6]) for d, _c in idx)   # 末5行label NaN
    assert np.isfinite(y).all()


def test_predict_index_last_window_per_code():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    p = _panel(["SH600000", "SZ000001"], dates, ["f1"])
    X, codes = predict_index(p, ["f1"], seq_len=4, eval_date=pd.Timestamp(dates[-1]))
    assert X.shape == (2, 4, 1) and X.dtype == np.float32
    assert set(codes) == {"SH600000", "SZ000001"}
    # 末窗右端 = eval_date 的行序号(=9)
    assert abs(float(X[0, -1, 0]) - 9.0) < 1e-6


def test_predict_index_cuts_future_and_skips_short():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    p = _panel(["SH600000"], dates, ["f1"])
    # eval_date 取中间 → 末窗右端 = 该日;且不含未来
    X, codes = predict_index(p, ["f1"], seq_len=3, eval_date=pd.Timestamp(dates[5]))
    assert X.shape == (1, 3, 1)
    assert abs(float(X[0, -1, 0]) - 5.0) < 1e-6
    # seq_len 比可用历史长 → 该 code 被跳
    X2, codes2 = predict_index(p, ["f1"], seq_len=20, eval_date=pd.Timestamp(dates[-1]))
    assert X2.shape[0] == 0 and codes2 == []
