import pandas as pd
from guanlan_v2.screen.factor_vintage import cs_vintage_from_frame, load_cs_vintage


def _frame():
    return pd.DataFrame([
        {"id": "mom_20", "date": "2026-01-05", "ic": 0.10, "n": 250, "realized_date": "2026-01-12"},
        {"id": "mom_20", "date": "2026-01-06", "ic": 0.20, "n": 250, "realized_date": "2026-01-13"},
        {"id": "mom_20", "date": "2026-01-07", "ic": -0.30, "n": 250, "realized_date": "2026-02-20"},
    ])


def test_cs_vintage_only_realized():
    r = cs_vintage_from_frame(_frame(), "mom_20", "2026-01-15", window=60, horizon=5, min_n=2)
    assert r is not None
    assert abs(r["ic"] - 0.15) < 1e-9   # mean(0.10,0.20),绝不含 -0.30(2026-02-20>D)
    assert r["n"] == 2


def test_cs_vintage_min_n_honest_none():
    assert cs_vintage_from_frame(_frame(), "mom_20", "2026-01-15", window=60, horizon=5, min_n=3) is None


def test_cs_vintage_trailing_window():
    f = pd.DataFrame([{"id": "x", "date": f"2026-01-{d:02d}", "ic": 0.01 * i, "n": 200,
                       "realized_date": f"2026-01-{d:02d}"} for i, d in enumerate(range(1, 11), 0)])
    r = cs_vintage_from_frame(f, "x", "2026-02-01", window=3, horizon=0, min_n=1)
    assert r["n"] == 3
    assert abs(r["ic"] - (0.07 + 0.08 + 0.09) / 3) < 1e-9


def test_cs_vintage_missing_factor_none():
    assert cs_vintage_from_frame(_frame(), "no_such", "2026-01-15", min_n=1) is None


def test_load_cs_vintage_missing_file_none(monkeypatch):
    # 缺产物文件 → None(诚实降级,不崩)
    import guanlan_v2.screen.factor_vintage as fv
    monkeypatch.setattr(fv, "CS_IC_PARQUET", fv.CS_IC_PARQUET.parent / "no_such_vintage.parquet")
    fv._cs_cache["mtime"] = None
    assert load_cs_vintage() is None


from guanlan_v2.screen.factor_vintage import tsic_vintage_from_frame


def _tsic_frame():
    rows = []
    vals = [(1.0, 0.01), (2.0, 0.02), (3.0, 0.03), (4.0, 0.04), (5.0, 0.05)]  # fval↑ fwd↑ → +1
    for i, (fv, fw) in enumerate(vals):
        d = f"2026-01-{i+1:02d}"
        rows.append({"code": "SH605358", "id": "mom_20", "date": d, "fval": fv,
                     "fwd": fw, "realized_date": d})
    rows.append({"code": "SH605358", "id": "mom_20", "date": "2026-01-20", "fval": 99.0,
                 "fwd": -9.0, "realized_date": "2026-03-01"})  # 未来实现,必排除
    return pd.DataFrame(rows)


def test_tsic_perfect_positive_pit():
    r = tsic_vintage_from_frame(_tsic_frame(), "SH605358", "mom_20", "2026-01-31",
                                window=60, horizon=5, min_n=4)
    assert r is not None and abs(r["ic"] - 1.0) < 1e-9   # 单调正→Spearman=1,且不含 03-01 那行
    assert r["n"] == 5


def test_tsic_scoped_code_miss():
    assert tsic_vintage_from_frame(_tsic_frame(), "SZ000001", "mom_20", "2026-01-31", min_n=1) is None


def test_tsic_min_n_none():
    assert tsic_vintage_from_frame(_tsic_frame(), "SH605358", "mom_20", "2026-01-31", min_n=10) is None


from guanlan_v2.screen.factor_vintage import factor_z_from_frame


def _zframe():
    import pandas as pd
    rows = [{"code": "SH605358", "id": "mom_20", "date": f"2026-01-{d:02d}", "fval": v}
            for d, v in zip(range(1, 12), [1, 1, 1, 1.5, 1, 1, 1, 1, 1, 1, 6])]  # 前5窗非常量,末=6
    rows.append({"code": "SH605358", "id": "mom_20", "date": "2026-02-01", "fval": 99.0})  # 未来,排除
    return pd.DataFrame(rows)


def test_factor_z_pit_and_value():
    r = factor_z_from_frame(_zframe(), "SH605358", "mom_20", "2026-01-11", window=60, min_n=5)
    assert r is not None
    assert r["z"] > 1.0 and r["fval"] == 6.0 and r["n"] == 11
    assert r["asof"] == "2026-01-11"   # 不含 2026-02-01


def test_factor_z_excludes_future_date():
    r = factor_z_from_frame(_zframe(), "SH605358", "mom_20", "2026-01-05", window=60, min_n=3)
    assert r is not None and r["fval"] == 1.0 and r["n"] == 5


def test_factor_z_min_n_none():
    assert factor_z_from_frame(_zframe(), "SH605358", "mom_20", "2026-01-03", window=60, min_n=5) is None


def test_factor_z_constant_std_zero_none():
    import pandas as pd
    flat = pd.DataFrame([{"code": "SH605358", "id": "x", "date": f"2026-01-{d:02d}", "fval": 2.0}
                         for d in range(1, 11)])
    assert factor_z_from_frame(flat, "SH605358", "x", "2026-01-10", min_n=3) is None   # std=0 → None


from guanlan_v2.screen.factor_vintage import _realized_map


def test_realized_map_horizon_shift():
    dts = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]
    m = _realized_map(dts, horizon=2)
    assert m["2026-01-05"] == "2026-01-07"     # +2 交易日
    assert m["2026-01-06"] == "2026-01-08"
    assert "2026-01-09" not in m and "2026-01-12" not in m   # 尾部 horizon 天无实现日 → 不入
