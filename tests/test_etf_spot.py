import pandas as pd
from financial_analyst.data.updaters import etf_spot


def test_spot_writes_parquet(tmp_path, monkeypatch):
    fake = pd.DataFrame({"代码": ["510300"], "IOPV实时估值": [4.93], "基金折价率": [-0.2],
                         "最新份额": [1.2e10], "总市值": [9e10], "换手率": [3.5]})
    monkeypatch.setattr(etf_spot, "_akshare_spot", lambda: fake)
    etf_spot.update_etf_spot(["SH510300"], parquet_root=tmp_path, asof="2026-05-29")
    df = pd.read_parquet(tmp_path / "etf_spot.parquet")
    row = df[df["ts_code"] == "SH510300"].iloc[0]
    assert {"ts_code", "asof", "iopv", "premium_discount_pct", "shares", "aum"} <= set(df.columns)
    assert abs(row["premium_discount_pct"] - (-0.2)) < 1e-6
