# tests/test_factor_ls.py
# 族多空序列门禁:L/S 手算值 / PIT available_date=t+1 / 末日不出行 / 截面太薄诚实缺席。
import numpy as np
import pandas as pd
import pytest
from guanlan_v2.strategy.compute import factor_ls as FL


def test_ls_series_value_and_pit():
    idx = pd.bdate_range("2025-01-01", periods=4)
    codes = list("ABCDE")
    close = pd.DataFrame(
        [[10, 10, 10, 10, 10],
         [11, 10, 10, 10, 9],    # t0→t1:A +10%,E −10%
         [11, 10, 10, 10, 9],
         [11, 10, 10, 10, 9]], index=idx, columns=codes, dtype=float)
    fac = pd.DataFrame([[5, 4, 3, 2, 1]] * 4, index=idx, columns=codes, dtype=float)
    out = FL.ls_series(fac, close, q=0.2, min_n=5)
    r0 = out[out["date"] == idx[0]].iloc[0]
    assert r0["ls_ret"] == pytest.approx(0.2)          # top=A(+10%) − bot=E(−10%)
    assert r0["available_date"] == idx[1]              # t+1 收盘才 realized(PIT)
    assert idx[-1] not in set(out["date"])             # 末日无次日收益 → 不出行


def test_ls_series_thin_cross_section_honest():
    idx = pd.bdate_range("2025-01-01", periods=5)
    close = pd.DataFrame(1.0, index=idx, columns=list("ABC"))
    fac = close.copy()
    assert FL.ls_series(fac, close).empty              # 默认 min_n=30 → 3 票诚实缺席


def test_load_family_ls_equal_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(FL, "FACTOR_LS_PARQUET", tmp_path / "ls.parquet")
    idx = pd.bdate_range("2025-01-01", periods=2)
    df = pd.DataFrame({
        "date": list(idx) * 2, "family": ["技术"] * 4,
        "factor_id": ["f1", "f1", "f2", "f2"],
        "ls_ret": [0.01, 0.02, 0.03, 0.04],
        "available_date": [idx[1], idx[1], idx[1], idx[1]]})
    df.to_parquet(tmp_path / "ls.parquet", index=False)
    g = FL.load_family_ls()
    assert g[g["date"] == idx[0]]["ls_ret"].iloc[0] == pytest.approx(0.02)   # (0.01+0.03)/2
