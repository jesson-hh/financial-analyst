"""离线 compute 路径的量纲校准单测(units.normalize_frame_units 纯函数,进程内可测)。

口径与 engine _normalize_vol_units 同检测带:r=(amount/close)/vol,
[50,200]→vol×100;[0.05,0.2]→vol×100+amount×1000;ref_vol 模式只修 amount。
"""
import pandas as pd

from guanlan_v2.strategy.compute.units import normalize_frame_units

_IDX = pd.to_datetime(["2026-03-13", "2026-03-16", "2026-03-31", "2026-04-01"])


def _mk(vol, amount, close=100.0):
    return pd.DataFrame({"close": [close] * len(vol), "volume": vol, "amount": amount},
                        index=_IDX[: len(vol)])


def test_direct_mode_hand_and_dual():
    # row0 正常;row1 双错(手+千元 r=0.1);row2 手(r=100);row3 正常
    df = _mk([1_000_000.0, 10_000.0, 10_000.0, 1_050_000.0],
             [100_000_000.0, 100_000.0, 100_000_000.0, 100_000_000.0])
    out = normalize_frame_units(df, vol_col="volume")
    assert out["volume"].tolist() == [1_000_000.0, 1_000_000.0, 1_000_000.0, 1_050_000.0]
    assert out.loc[_IDX[1], "amount"] == 100_000_000.0      # 千元 → 元
    assert out.loc[_IDX[2], "amount"] == 100_000_000.0      # 手批次 amount 本对,不动


def test_ref_vol_mode_fixes_amount_only():
    # breadth/mainline 形态:df 只有 close+amount,vol 由 ref 提供
    df = pd.DataFrame({"close": [100.0, 100.0],
                       "amount": [100_000.0, 100_000_000.0]}, index=_IDX[:2])
    ref = pd.Series([10_000.0, 1_000_000.0], index=_IDX[:2])
    out = normalize_frame_units(df, ref_vol=ref)
    assert out.loc[_IDX[0], "amount"] == 100_000_000.0      # dual → ×1000
    assert out.loc[_IDX[1], "amount"] == 100_000_000.0      # 正常不动
    assert "volume" not in out.columns                       # ref 不写回


def test_graceful_passthrough():
    assert normalize_frame_units(pd.DataFrame()).empty
    nocol = pd.DataFrame({"close": [1.0]}, index=_IDX[:1])
    assert normalize_frame_units(nocol).equals(nocol)
    # 无 vol 列且无 ref → 原样
    noref = pd.DataFrame({"close": [100.0], "amount": [100_000.0]}, index=_IDX[:1])
    assert normalize_frame_units(noref)["amount"].tolist() == [100_000.0]
    # NaN/零量不动
    df = _mk([0.0, float("nan")], [0.0, 1.0])
    out = normalize_frame_units(df, vol_col="volume")
    assert out["volume"].fillna(-1).tolist() == [0.0, -1.0]


def test_pillars_pin_normalize():
    # 契约钉:三支柱面板构建必须过 normalize_frame_units(防回退)
    for fn in (r"G:\guanlan-v2\guanlan_v2\strategy\compute\v4.py",
               r"G:\guanlan-v2\guanlan_v2\strategy\compute\breadth.py",
               r"G:\guanlan-v2\guanlan_v2\strategy\compute\mainline.py"):
        src = open(fn, encoding="utf-8").read()
        assert "normalize_frame_units(" in src, fn
