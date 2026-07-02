# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd


def _quotes_for(codes, trend=0.02, days=45):
    out = {}
    dates = pd.date_range("2026-05-01", periods=days, freq="B")
    for i, c in enumerate(codes):
        base = 10 + i
        close = base * (1 + trend) ** np.arange(days)
        out[c] = pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d"), "close": close,
                               "amount": np.full(days, 1e8)})
    return out


def test_quant_signals_with_injected_quotes():
    from guanlan_v2.industry.framework import load_framework, segment_pool
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    pool = segment_pool(fw, "C2")
    quotes = _quotes_for(pool)
    sig = quant_signals(fw, quotes=quotes)
    c2 = sig["C2"]
    assert c2["momentum20"] is not None and c2["momentum20"] > 0.3   # 每日+2%,20日≈+48.6%
    assert c2["breadth"] == 1.0
    assert c2["quote_date"] == quotes[pool[0]]["trade_date"].iloc[-1]
    assert ("v4_pct_mean" in c2) and ("excess20" in c2)   # 产物缺→None+reason 而非崩


def test_adjacent_stub_excluded():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    sig = quant_signals(fw, quotes={})
    assert "G3" not in sig and "G4" not in sig


def test_empty_quotes_honest():
    from guanlan_v2.industry.framework import load_framework
    from guanlan_v2.industry.aggregate import quant_signals
    fw = load_framework()
    sig = quant_signals(fw, quotes={})
    assert sig["C2"]["momentum20"] is None and sig["C2"]["reason"]
