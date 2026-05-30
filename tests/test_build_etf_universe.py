import pandas as pd
from financial_analyst.data import etf_universe


def test_build_merges_seed_and_topaum(monkeypatch, tmp_path):
    fake = pd.DataFrame({"代码": ["510300", "159915", "999999"],
                         "总市值": [9e10, 8e10, 1e5], "换手率": [5.0, 4.0, 0.01]})
    monkeypatch.setattr(etf_universe, "_akshare_spot", lambda: fake)
    out = tmp_path / "etf.txt"
    codes = etf_universe.build_etf_universe(top_n=2, seed=["SH510300", "SZ159001"], out_path=out)
    assert "SH510300" in codes and "SZ159001" in codes and "SZ159915" in codes
    assert "SZ999999" not in codes
    assert out.read_text(encoding="utf-8").strip()
