from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest


def _stub_loader(counter):
    class L:
        def fetch_quote(self, code, start, end, freq="day"):
            counter["n"] += 1
            dates = pd.date_range("2024-01-02", periods=20, freq="B")
            df = pd.DataFrame({"open": np.arange(20), "high": np.arange(20),
                               "low": np.arange(20), "close": np.arange(20),
                               "volume": np.full(20, 1e6)}, index=dates)
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()
    return L()


@pytest.fixture(autouse=True)
def _clear():
    from financial_analyst.factors.zoo.panel_cache import clear_panel_cache
    clear_panel_cache()
    yield
    clear_panel_cache()


def test_hit_reuses_panel():
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    cnt = {"n": 0}
    loader = _stub_loader(cnt)
    codes = ["SH600000", "SZ000001"]
    p1 = load_panel_cached(loader, codes, "2024-01-01", "2024-02-01")
    p2 = load_panel_cached(loader, codes, "2024-01-01", "2024-02-01")
    assert p1 is p2
    assert cnt["n"] == 2


def test_miss_on_different_window():
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    cnt = {"n": 0}
    loader = _stub_loader(cnt)
    codes = ["SH600000"]
    load_panel_cached(loader, codes, "2024-01-01", "2024-02-01")
    load_panel_cached(loader, codes, "2024-01-01", "2024-03-01")
    assert cnt["n"] == 2


def test_lru_evicts_oldest():
    from financial_analyst.factors.zoo import panel_cache as pc
    pc.clear_panel_cache()
    cnt = {"n": 0}
    loader = _stub_loader(cnt)
    for i in range(pc._MAXSIZE + 1):
        pc.load_panel_cached(loader, [f"SH60{i:04d}"], "2024-01-01", "2024-02-01")
    n_before = cnt["n"]
    pc.load_panel_cached(loader, ["SH600000"], "2024-01-01", "2024-02-01")
    assert cnt["n"] == n_before + 1


def test_concurrent_no_crash():
    from financial_analyst.factors.zoo.panel_cache import load_panel_cached
    cnt = {"n": 0}
    loader = _stub_loader(cnt)
    codes = ["SH600000", "SZ000001"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(lambda _: load_panel_cached(loader, codes, "2024-01-01", "2024-02-01"), range(32)))
    assert all(r is not None for r in res)
