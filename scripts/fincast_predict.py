# -*- coding: utf-8 -*-
"""FinCast v1 零样本每日批量推理(guanlan 自有·港移自 stocks tsfm_exp)。

用 conda stocks GPU 解释器跑:
    D:/app/miniconda/envs/stocks/python.exe scripts/fincast_predict.py --date 2026-06-22

读 guanlan 自己的 close(QlibBinaryLoader 直读二进制)→ FinCast(vendor/fincast_repo + v1.pth · GPU)
→ pred_ret_5d → 直写 var/v4_fincast_pred.parquet(Spec 1 DL 集成层契约;去 sync)。
**命门**:GPU 推理离线;9999 请求路径绝不跑模型。

setup(一次性 vendor 拷贝)见 scripts/setup_fincast.md。
"""
import argparse
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "engine"))                            # financial_analyst
sys.path.insert(0, str(_REPO / "vendor" / "fincast_repo" / "src"))  # tools/ffm/data_tools

from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader  # noqa: E402
from guanlan_v2.strategy.compute.breadth import list_all_instruments     # noqa: E402
from guanlan_v2.strategy.compute.regen import DEFAULT_PROVIDER, _latest_trade_date  # noqa: E402
from guanlan_v2.strategy.compute.fincast_io import build_context_matrix, write_pred_rolling  # noqa: E402

CONTEXT_LEN, HORIZON, BATCH = 512, 5, 64
WEIGHTS = str(_REPO / "vendor" / "models" / "fincast" / "v1.pth")
OUT = str(_REPO / "var" / "v4_fincast_pred.parquet")


class FinCastAdapter:
    """港移自 stocks zero_shot_daily.FinCastAdapter(逐字一致 forecast 口径):
    vendored 代码(vendor/fincast_repo/src)+ v1.pth · GPU。

    输入: (B, T) float32 close context;输出: (B,) 预测 horizon 日收益。
    """
    def __init__(self, weights: str, horizon: int = HORIZON, context_len: int = CONTEXT_LEN):
        if not os.path.exists(weights):
            raise FileNotFoundError(f"FinCast 权重缺:{weights}(见 scripts/setup_fincast.md)")
        from tools.inference_utils import get_model_api   # noqa: WPS433 (vendor/fincast_repo/src)
        cfg = SimpleNamespace(
            backend="gpu", model_path=weights, model_version="v1",
            horizon_len=horizon, context_len=min(max(context_len, 32), 1024),  # 32-1024 倍数of32
            num_experts=4, gating_top_n=2, load_from_compile=True, forecast_mode="mean",
        )
        print(f"[fincast] 加载 {weights} ...", flush=True)
        self.ffm_api = get_model_api(cfg)
        self.horizon = horizon

    def predict(self, contexts: np.ndarray, horizon: int) -> np.ndarray:
        import torch   # noqa: WPS433
        # ffm_api.forecast(list[1d array], list[freq=0]) -> (mean, full)
        past_list = [c.astype(np.float32) for c in contexts]
        with torch.inference_mode():
            out, _full = self.ffm_api.forecast(past_list, [0] * len(past_list))
        if isinstance(out, torch.Tensor):
            out = out.detach().cpu().numpy()
        else:
            out = np.asarray(out)
        # out shape: [B, H'];H' 可能含 context 上预测(return_forecast_on_context=True),
        # 取最后 horizon 个为未来预测,末日价转 5 日收益。
        horizon_arr = out[:, -horizon:]
        last_close = contexts[:, -1]
        end_price = horizon_arr[:, -1]
        return end_price / last_close - 1.0


def _read_close_panel(loader, codes, eval_date, context_len):
    """逐码 _read_bin(code,'close')(= breadth 路径)→ (datetime × instrument) close 面板,
    截到 ≤ eval_date(不看未来)。"""
    series = {}
    for code in codes:
        try:
            c = loader._read_bin(code, "close")            # datetime 索引 Series(mirror breadth.py:88)
            if c is not None and len(c):
                series[code] = c
        except Exception:                                   # noqa: BLE001 — 单码读失败跳过
            continue
    if not series:
        raise RuntimeError("无任何可读 close(检查 provider_uri)")
    panel = pd.DataFrame(series).sort_index()
    panel.index = pd.DatetimeIndex(panel.index)
    return panel.loc[:pd.Timestamp(eval_date)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="评估日 YYYY-MM-DD(缺省=guanlan 最新交易日)")
    ap.add_argument("--context-len", type=int, default=CONTEXT_LEN)
    ap.add_argument("--horizon", type=int, default=HORIZON)
    ap.add_argument("--batch-size", type=int, default=BATCH)
    ap.add_argument("--min-valid-frac", type=float, default=0.9)
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    args = ap.parse_args()

    eval_date = args.date or _latest_trade_date(args.provider)
    print(f"评估日 {eval_date} · provider {args.provider}", flush=True)
    loader = QlibBinaryLoader(args.provider)
    codes = list_all_instruments(args.provider)
    print(f"全市场 {len(codes)} 码,读 close 面板 ...", flush=True)
    panel = _read_close_panel(loader, codes, eval_date, args.context_len)
    chosen, arr = build_context_matrix(panel, eval_date, args.context_len, args.min_valid_frac)
    print(f"有效标的 {len(chosen)} 只 · 窗口 {arr.shape[1]} 日,加载 FinCast ...", flush=True)

    adapter = FinCastAdapter(WEIGHTS, horizon=args.horizon, context_len=args.context_len)
    t0 = time.time()
    preds = []
    for b in range(0, len(chosen), args.batch_size):
        batch = arr[b:b + args.batch_size]
        preds.append(np.asarray(adapter.predict(batch, args.horizon), dtype=np.float32))
        done = min(b + args.batch_size, len(chosen))
        if (b // args.batch_size) % 10 == 0 or done == len(chosen):
            print(f"  {done}/{len(chosen)} 耗时 {time.time() - t0:.1f}s", flush=True)
    preds = np.concatenate(preds)
    print(f"完成 {len(preds)} 条 · 均值 {preds.mean():+.4f} · {time.time() - t0:.1f}s", flush=True)

    out = write_pred_rolling(OUT, eval_date, chosen, preds, keep_days=60)
    print(f"已写 {OUT}({len(out)} 条 · {pd.to_datetime(out['eval_date']).nunique()} 日)", flush=True)


if __name__ == "__main__":
    main()
