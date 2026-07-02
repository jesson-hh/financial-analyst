"""全A等权基准产物单测(P1 §1)。fake loader 注入,零真实取数。"""
import pandas as pd
import pytest


class _FakeLoader:
    def __init__(self, series_by_code):
        self.s = series_by_code

    def _read_bin(self, code, field):
        assert field == "close"
        return self.s.get(code)


def _mk(dates, vals):
    return pd.Series([float(v) if v == v else float("nan") for v in vals],
                     index=pd.to_datetime(dates), dtype="float64")


_D = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]


def test_compute_mean_and_n(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    loader = _FakeLoader({
        "SHA": _mk(_D, [10, 11, 11, 12]),            # ret: -, +10%, 0%, +9.0909%
        "SHB": _mk(_D, [20, 20, float("nan"), 22]),  # ret: -, 0%, NaN(停牌), NaN(复牌prev=NaN)
    })
    n = EQ.compute_eqw_market("ignored", end=_D[-1], codes=["SHA", "SHB"], start=_D[0],
                              loader=loader)
    df = pd.read_parquet(tmp_path / "eqw.parquet")
    assert n == len(df) == 3                                  # 首日无 ret,不落
    r2 = df[df["date"] == "2026-06-02"].iloc[0]
    assert r2["ret"] == pytest.approx((0.10 + 0.0) / 2) and r2["n"] == 2
    r3 = df[df["date"] == "2026-06-03"].iloc[0]
    assert r3["ret"] == pytest.approx(0.0) and r3["n"] == 1   # B 停牌剔除,不当 0 收益
    r4 = df[df["date"] == "2026-06-04"].iloc[0]
    assert r4["n"] == 1                                        # B 复牌首日 prev=NaN 保守剔除


def test_compute_idempotent_and_all_nan_code(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    loader = _FakeLoader({"SHA": _mk(_D, [10, 11, 12, 13]),
                          "SHZ": _mk(_D, [float("nan")] * 4), "SHY": None})
    n1 = EQ.compute_eqw_market("x", end=_D[-1], codes=["SHA", "SHZ", "SHY"], start=_D[0],
                               loader=loader)
    n2 = EQ.compute_eqw_market("x", end=_D[-1], codes=["SHA", "SHZ", "SHY"], start=_D[0],
                               loader=loader)
    assert n1 == n2 == 3                                       # 幂等覆盖;坏票不炸


def test_compute_no_codes_raises(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    with pytest.raises(RuntimeError):
        EQ.compute_eqw_market("x", end=_D[-1], codes=["NOPE"], start=_D[0],
                              loader=_FakeLoader({}))


def test_load_missing_and_cache(monkeypatch, tmp_path):
    import guanlan_v2.strategy.compute.eqw_market as EQ
    monkeypatch.setattr(EQ, "EQW_MARKET_RET_PARQUET", tmp_path / "eqw.parquet")
    monkeypatch.setattr(EQ, "_eqw_cache", {"mtime": None, "df": None})
    assert EQ.load_eqw_ret() is None                           # 缺失=None 诚实缺席
    EQ.compute_eqw_market("x", end=_D[-1], codes=["SHA"], start=_D[0],
                          loader=_FakeLoader({"SHA": _mk(_D, [10, 11, 12, 13])}))
    df1 = EQ.load_eqw_ret()
    assert df1 is not None and EQ.load_eqw_ret() is df1        # mtime 缓存同对象


def test_eqw_cum_ret_windows():
    import guanlan_v2.strategy.compute.eqw_market as EQ
    df = pd.DataFrame({"date": ["2026-06-02", "2026-06-03", "2026-06-04"],
                       "ret": [0.01, 0.02, -0.01], "n": [100, 100, 100]})
    got = EQ.eqw_cum_ret(df, "2026-06-02", "2026-06-04")       # (entry, exit] = 03,04
    assert got == pytest.approx(1.02 * 0.99 - 1)
    assert EQ.eqw_cum_ret(df, "2026-06-02", "2026-06-05") is None   # 尾部不覆盖
    assert EQ.eqw_cum_ret(df, "2026-05-01", "2026-06-03") is None   # 头部不覆盖
    assert EQ.eqw_cum_ret(df, "2026-06-04", "2026-06-04") is None   # 空窗
    assert EQ.eqw_cum_ret(None, "2026-06-02", "2026-06-04") is None # 产物缺席
