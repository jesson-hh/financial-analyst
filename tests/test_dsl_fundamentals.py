from __future__ import annotations
import json
import numpy as np
import pandas as pd
import pytest
from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, compile_factor
from financial_analyst.factors.zoo import PanelData


def _fund_panel():
    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(2)
    close = pd.Series(50.0, index=idx)
    df = pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close,
        "volume": pd.Series(1e6, index=idx),
        "pe_ttm": pd.Series(rng.uniform(8, 40, len(idx)), index=idx),
        "pb": pd.Series(rng.uniform(0.8, 5, len(idx)), index=idx),
        "dv_ttm": pd.Series(rng.uniform(0, 5, len(idx)), index=idx),
        "total_mv": pd.Series(rng.uniform(1e6, 5e7, len(idx)), index=idx),
        "ps_ttm": pd.Series(rng.uniform(1, 10, len(idx)), index=idx),
        "circ_mv": pd.Series(rng.uniform(8e5, 4e7, len(idx)), index=idx),
        "turnover_rate": pd.Series(rng.uniform(0.3, 8, len(idx)), index=idx),
    })
    return PanelData(df)


def test_vocab_lists_fundamentals():
    for f in ["pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate"]:
        assert f in FACTOR_VOCAB


@pytest.mark.parametrize("expr", [
    "rank(-pe_ttm)",
    "rank(dv_ttm)",
    "rank(-total_mv)",
    "rank(-pb) * rank(dv_ttm)",
    "rank(-ps_ttm)",        # all 7 fundamentals exercised through the compile ns
    "rank(-circ_mv)",
    "rank(turnover_rate)",
])
def test_compile_fundamental_expr(expr):
    fn = compile_factor(expr)
    out = fn(_fund_panel())
    assert isinstance(out, pd.Series)
    assert out.index.names == ["datetime", "code"]
    assert out.notna().any()


def test_forge_fundamental_not_out_of_vocab():
    from financial_analyst.factors.forge.forge import forge_factor
    good = json.dumps({"expr": "rank(dv_ttm)", "parsed": [{"k": "方向", "v": "高股息"}],
                       "name": "usr_divyield", "rationale": "股息率排序", "out_of_vocab": False})
    r = forge_factor("高股息", complete_fn=lambda messages: good)
    assert r.compile_ok is True
    assert r.out_of_vocab is False
    assert r.expr == "rank(dv_ttm)"
