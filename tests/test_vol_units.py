"""成交量量纲校准单元测试(子进程强制仓内 engine——venv .pth 指旧仓的已知坑)。

口径:vol 应为「股」、amount 应为「元」;r=(amount/close)/vol 自检:
≈1 正常不动;∈[50,200] vol=手 → ×100;∈[0.05,0.2] vol=手+amount=千元 → vol×100,amount×1000。
"""
import subprocess

_PY = r"G:\financial-analyst\.venv\Scripts\python.exe"

_SCRIPT = r'''
import sys; sys.path.insert(0, r"G:/guanlan-v2/engine")
import pandas as pd
import financial_analyst.data.loaders.qlib_binary as qb
assert "guanlan-v2" in qb.__file__.replace("\\", "/"), qb.__file__
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
    r = subprocess.run([_PY, "-c", _SCRIPT], capture_output=True, text=True, timeout=120)
    assert "VOLNORM OK" in (r.stdout or ""), (r.stdout or "") + (r.stderr or "")


def test_fetch_quote_pins_normalize():
    # 契约钉:fetch_quote 出口必须经 _normalize_vol_units + 分钟频交叉定标(防回退)
    src = open(r"G:\guanlan-v2\engine\financial_analyst\data\loaders\qlib_binary.py",
               encoding="utf-8").read()
    assert "def _normalize_vol_units" in src
    body = src.split("def fetch_quote")[1].split("\n    def ")[0]
    assert "_normalize_vol_units(" in body
    assert "_crosscheck_intraday_vol(" in body


_XDAY_SCRIPT = r'''
import sys; sys.path.insert(0, r"G:/guanlan-v2/engine")
import pandas as pd
import financial_analyst.data.loaders.qlib_binary as qb
assert "guanlan-v2" in qb.__file__.replace("\\", "/"), qb.__file__

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

# —— 真数据集成:立昂微 5min(06-10/06-11 amount 全缺 + 手批次)——
ld2 = qb.QlibBinaryLoader({"day": r"G:/stocks/stock_data/cn_data",
                           "5min": r"G:/stocks/stock_data/cn_data_5min"})
df = ld2.fetch_quote("SH605358", "2026-06-08", "2026-06-12", "5min")
if len(df):
    day = df["trade_date"].astype(str).str[:10]
    ref = ld2.fetch_quote("SH605358", "2026-06-08", "2026-06-12", "day")
    rv = {str(t)[:10]: float(v) for t, v in zip(ref["trade_date"], ref["vol"]) if v == v and v > 0}
    for d, g in df.groupby(day):
        s = float(g["vol"].sum()); dv = rv.get(d)
        if dv and s > 0:
            r = dv / s
            assert 0.5 <= r <= 2.0, (d, r, s, dv)
    print("XDAY REAL OK")
else:
    print("XDAY REAL SKIP(无5min数据)")
'''


def test_crosscheck_intraday_vol_subprocess():
    r = subprocess.run([_PY, "-c", _XDAY_SCRIPT], capture_output=True, text=True, timeout=180)
    assert "XDAY OK" in (r.stdout or ""), (r.stdout or "") + (r.stderr or "")
    assert "XDAY REAL" in (r.stdout or ""), (r.stdout or "") + (r.stderr or "")
