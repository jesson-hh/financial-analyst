# tests/test_gat_scripts.py
# 脚本轻量门禁:gat_predict 可 import/--help(argparse);gat_validate 端到端跑合成 parquet 出 JSON。
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent


def test_gat_predict_help_exits_zero():
    # 显式 utf-8 + replace:text=True 在 Windows 用 GBK 解码,而按仓规跑测试带
    # PYTHONIOENCODING=utf-8 时子进程 help(含中文)输出 UTF-8 → reader 线程
    # GBK 解码炸 → r.stdout=None → TypeError。断言只看 ASCII 旗名,replace 两头安全。
    r = subprocess.run([sys.executable, str(_REPO / "scripts" / "gat_predict.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_REPO))
    assert r.returncode == 0
    assert "--date" in r.stdout and "--device" in r.stdout


def test_gat_validate_runs_on_synthetic(tmp_path, monkeypatch):
    # 造合成 gat parquet,桩掉引擎前向收益,直接调函数(脚本同一入口)验证闸可跑
    from guanlan_v2.strategy.compute import cpcv
    dates = pd.bdate_range("2026-01-05", periods=40)
    rng = np.random.default_rng(0)
    rows = [{"eval_date": d, "instrument": f"C{i:03d}", "pred_ret_5d": float(rng.normal())}
            for d in dates for i in range(60)]
    p = tmp_path / "dl_pred_gat.parquet"
    pd.DataFrame(rows).to_parquet(p, index=False)
    monkeypatch.setattr(cpcv, "_fwd_returns_for_snapshots",
                        lambda hist, horizon=5: {(str(r.date), str(r.code)): float(r.score) * 0.1
                                                 for r in hist.itertuples()})
    out = cpcv.validate_dl_source(str(p))
    assert out["ready"] is True and "dsr" in out and "passes_gate" in out
