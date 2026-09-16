"""成交量量纲校准单元测试(子进程强制仓内 engine——venv .pth 指旧仓的已知坑)。

口径:vol 应为「股」、amount 应为「元」;r=(amount/close)/vol 自检:
≈1 正常不动;∈[50,200] vol=手 → ×100;∈[0.05,0.2] vol=手+amount=千元 → vol×100,amount×1000。
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _run_script(script: str, *, timeout: int = 120):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_ROOT / "engine") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", script], cwd=_ROOT, env=env,
        capture_output=True, text=True, timeout=timeout,
    )

_SCRIPT = r'''
import pandas as pd
import financial_analyst.data.loaders.qlib_binary as qb
df = pd.DataFrame({
    "close":  [100.0, 100.0, 100.0, 100.0, float("nan"), 100.0],
    "vol":    [1_000_000.0, 10_000.0, 10_000.0, 0.0, 5.0, 1_050_000.0],
    "amount": [100_000_000.0, 100_000_000.0, 100_000.0, 0.0, 1.0, 100_000_000.0],
})
# row0 正常(股+元) r=1 不动;row1 vol=手 r=100 → vol×100
# row2 双错(手+千元) r=0.1 → vol×100, amount×1000;row3 零量停牌不动
# row4 close NaN 不动;row5 r≈0.95(正常 VWAP 偏离)不动
out = qb._normalize_vol_units(df.copy())
assert out.loc[0, "vol"] == 1_000_000 and out.loc[0, "amount"] == 100_000_000
assert out.loc[1, "vol"] == 1_000_000 and out.loc[1, "amount"] == 100_000_000
assert out.loc[2, "vol"] == 1_000_000 and abs(out.loc[2, "amount"] - 100_000_000) < 1e-6
assert out.loc[3, "vol"] == 0.0
assert out.loc[4, "vol"] == 5.0 and out.loc[4, "amount"] == 1.0
assert out.loc[5, "vol"] == 1_050_000.0
assert qb._normalize_vol_units(pd.DataFrame()).empty
nocol = pd.DataFrame({"close": [1.0]})
assert qb._normalize_vol_units(nocol).equals(nocol)
print("VOLNORM OK")
'''


def test_normalize_vol_units_subprocess():
    r = _run_script(_SCRIPT)
    assert "VOLNORM OK" in (r.stdout or ""), (r.stdout or "") + (r.stderr or "")


def test_fetch_quote_pins_normalize():
    # 契约钉:fetch_quote 出口必须经 _normalize_vol_units + 分钟频交叉定标(防回退)
    src = (_ROOT / "engine/financial_analyst/data/loaders/qlib_binary.py").read_text(
        encoding="utf-8"
    )
    assert "def _normalize_vol_units" in src
    body = src.split("def fetch_quote")[1].split("\n    def ")[0]
    assert "_normalize_vol_units(" in body
    assert "_crosscheck_intraday_vol(" in body


_XDAY_SCRIPT = r'''
import pandas as pd
import financial_analyst.data.loaders.qlib_binary as qb

# —— 合成单测:amount 整日全缺 → 用日线交叉定标 ——
class _Stub(qb.QlibBinaryLoader):
    def __init__(self):
        self._roots = {"day": "x"}
    def fetch_quote(self, code, start, end, freq="day"):
        assert freq == "day"
        return pd.DataFrame({"trade_date": pd.to_datetime(["2026-06-10", "2026-06-11"]),
                             "vol": [1_000_000.0, 2_000_000.0]})

ld = _Stub()
# 6-10 手批次(日合计 1万 vs 日线 100万 → ×100);6-11 本就是股(合计 200万,比值 1)不动
df5 = pd.DataFrame({
    "trade_date": pd.to_datetime(["2026-06-10 09:35", "2026-06-10 09:40",
                                  "2026-06-11 09:35", "2026-06-11 09:40"]),
    "vol": [6_000.0, 4_000.0, 1_200_000.0, 800_000.0],
    "amount": [float("nan")] * 4,
    "close": [10.0, 10.0, 10.0, 10.0],
})
out = ld._crosscheck_intraday_vol("TEST", df5.copy())
assert out["vol"].tolist() == [600_000.0, 400_000.0, 1_200_000.0, 800_000.0], out["vol"].tolist()
# 有 amount 的日不归此道管(r 自检已覆盖):不应触发日线参照
df_amt = pd.DataFrame({
    "trade_date": pd.to_datetime(["2026-06-10 09:35"]),
    "vol": [6_000.0], "amount": [600_000.0], "close": [10.0],
})
assert ld._crosscheck_intraday_vol("TEST", df_amt.copy())["vol"].tolist() == [6_000.0]
assert ld._crosscheck_intraday_vol("TEST", pd.DataFrame()) is not None
print("XDAY OK")
'''


def test_crosscheck_intraday_vol_subprocess():
    r = _run_script(_XDAY_SCRIPT, timeout=180)
    assert "XDAY OK" in (r.stdout or ""), (r.stdout or "") + (r.stderr or "")


@pytest.mark.realdata
def test_crosscheck_intraday_vol_realdata():
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.data.paths import get_data_paths

    paths = get_data_paths()
    if paths.qlib_5min is None or not paths.qlib_day.exists() or not paths.qlib_5min.exists():
        pytest.skip("configured day/5min Qlib roots are unavailable")
    loader = QlibBinaryLoader({"day": str(paths.qlib_day), "5min": str(paths.qlib_5min)})
    frame = loader.fetch_quote("SH605358", "2026-06-08", "2026-06-12", "5min")
    if frame.empty:
        pytest.skip("configured 5min dataset has no SH605358 sample")
    day = frame["trade_date"].astype(str).str[:10]
    reference = loader.fetch_quote("SH605358", "2026-06-08", "2026-06-12", "day")
    ref_vol = {
        str(ts)[:10]: float(vol)
        for ts, vol in zip(reference["trade_date"], reference["vol"])
        if vol == vol and vol > 0
    }
    for trade_day, group in frame.groupby(day):
        intraday = float(group["vol"].sum())
        daily = ref_vol.get(trade_day)
        if daily and intraday > 0:
            assert 0.5 <= daily / intraday <= 2.0
