# tests/test_fincast_io.py
# FinCast 港移纯函数门禁:close 面板→context 矩阵(末N日/有效标的过滤/ffill);rolling-keep 写出契约。
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.strategy.compute.fincast_io import build_context_matrix, write_pred_rolling  # noqa: E402


def _panel(dates, codes, fill=1.0):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({c: [fill] * len(idx) for c in codes}, index=idx)


def test_build_context_matrix_shape_and_tail():
    dates = pd.date_range("2024-01-01", periods=1000, freq="D")  # ≥512 日历史 ≤ eval_date
    panel = _panel(dates, ["SH600000", "SZ000001"])
    chosen, arr = build_context_matrix(panel, "2026-06-22", context_len=512, min_valid_frac=0.9)
    assert set(chosen) == {"SH600000", "SZ000001"}
    assert arr.shape == (2, 512)               # (N instruments, T context)
    assert arr.dtype == np.float32


def test_build_context_matrix_drops_invalid():
    dates = pd.date_range("2024-01-01", periods=1000, freq="D")  # ≥512 日历史 ≤ eval_date
    panel = _panel(dates, ["SH600000", "SZ000001"])
    panel["SZ000001"] = np.nan                  # 全 NaN → 无效
    chosen, arr = build_context_matrix(panel, "2026-06-22", context_len=512)
    assert chosen == ["SH600000"]
    assert arr.shape == (1, 512)


def test_build_context_matrix_cuts_to_eval_date_no_future():
    dates = pd.date_range("2026-01-01", periods=600, freq="D")
    panel = _panel(dates, ["SH600000"])
    # eval_date 取中间某天:窗口末日 = eval_date,不含未来
    chosen, arr = build_context_matrix(panel, "2026-05-01", context_len=100)
    assert arr.shape == (1, 100)
    # 不抛、窗口截到 eval_date(由 panel.loc[:eval_date] 保证)


def test_build_context_matrix_too_short_raises():
    dates = pd.date_range("2026-01-01", periods=50, freq="D")
    panel = _panel(dates, ["SH600000"])
    try:
        build_context_matrix(panel, "2026-02-15", context_len=512)
        assert False, "应抛 ValueError(面板太短)"
    except ValueError:
        pass


def test_write_pred_rolling_contract_and_overwrite(tmp_path):
    p = str(tmp_path / "v4_fincast_pred.parquet")
    # 首写
    df1 = write_pred_rolling(p, "2026-06-20", ["SH600000", "SZ000001"], [0.01, -0.02], keep_days=60)
    assert list(df1.columns) == ["eval_date", "instrument", "pred_ret_5d"]   # 扁平契约
    # on-disk eval_date 必须 datetime64(非 object Timestamp)— pyarrow22/conda stocks 否则崩
    assert pd.api.types.is_datetime64_any_dtype(pd.read_parquet(p)["eval_date"])
    # 同日重写覆盖(不重复)
    write_pred_rolling(p, "2026-06-20", ["SH600000"], [0.05], keep_days=60)
    out = pd.read_parquet(p)
    d20 = out[pd.to_datetime(out["eval_date"]) == pd.Timestamp("2026-06-20")]
    assert len(d20) == 1 and abs(float(d20.iloc[0]["pred_ret_5d"]) - 0.05) < 1e-6  # 覆盖,留最后(float32)
    # 新日期累加
    write_pred_rolling(p, "2026-06-21", ["SH600000"], [0.03], keep_days=60)
    out = pd.read_parquet(p)
    assert pd.to_datetime(out["eval_date"]).nunique() == 2


def test_write_pred_rolling_keep_days(tmp_path):
    p = str(tmp_path / "v4_fincast_pred.parquet")
    for i in range(5):
        write_pred_rolling(p, f"2026-06-{10+i:02d}", ["SH600000"], [0.01 * i], keep_days=3)
    out = pd.read_parquet(p)
    assert pd.to_datetime(out["eval_date"]).nunique() == 3   # 只保留最近 3 日
    assert pd.to_datetime(out["eval_date"]).max() == pd.Timestamp("2026-06-14")


def test_write_pred_rolling_with_train_cutoff(tmp_path):
    p = str(tmp_path / "dl_pred_lstm.parquet")
    write_pred_rolling(p, "2026-06-22", ["SH600000", "SZ000001"], [0.01, -0.02],
                       keep_days=60, train_cutoff="2026-06-15")
    out = pd.read_parquet(p)
    assert "train_cutoff" in out.columns
    assert pd.api.types.is_datetime64_any_dtype(out["train_cutoff"])
    assert (pd.to_datetime(out["train_cutoff"]) == pd.Timestamp("2026-06-15")).all()


def test_write_pred_rolling_without_cutoff_unchanged(tmp_path):
    p = str(tmp_path / "v4_fincast_pred.parquet")
    write_pred_rolling(p, "2026-06-22", ["SH600000"], [0.01], keep_days=60)
    out = pd.read_parquet(p)
    assert list(out.columns) == ["eval_date", "instrument", "pred_ret_5d"]   # 无 train_cutoff
