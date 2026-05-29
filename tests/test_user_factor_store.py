from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from financial_analyst.factors.forge.store import UserFactorStore
from financial_analyst.factors.zoo import PanelData
from financial_analyst.factors.zoo.registry import get as reg_get, _clear_registry_for_tests


def _panel():
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(1)
    close = pd.Series(rng.lognormal(0, 0.02, len(idx)), index=idx).groupby(level="code").cumprod() * 50 + 10
    return PanelData(pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                                   "close": close, "volume": pd.Series(1e6, index=idx)}))


def test_add_persists_and_registers(tmp_path):
    s = UserFactorStore(root=tmp_path)
    entry = s.add({"name": "usr_rev5", "family": "user", "expr": "rank(-delta(close,5))",
                   "description": "5日反转", "parsed": [], "kpis": {}})
    assert entry["name"] == "usr_rev5"
    assert (tmp_path / "user_factors.json").exists()
    assert UserFactorStore(root=tmp_path).list()[0]["name"] == "usr_rev5"
    spec = reg_get("usr_rev5")
    assert spec.family == "user"
    out = spec.compute(_panel())
    assert isinstance(out, pd.Series)


def test_reload_register_all(tmp_path):
    UserFactorStore(root=tmp_path).add({"name": "usr_x", "family": "user",
        "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    _clear_registry_for_tests()  # simulate fresh process
    with pytest.raises(KeyError):
        reg_get("usr_x")
    n = UserFactorStore(root=tmp_path).register_all()
    assert n == 1
    assert reg_get("usr_x").family == "user"


def test_dup_name_gets_suffix(tmp_path):
    s = UserFactorStore(root=tmp_path)
    a = s.add({"name": "usr_x", "family": "user", "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    b = s.add({"name": "usr_x", "family": "user", "expr": "rank(-close)", "description": "", "parsed": [], "kpis": {}})
    assert a["name"] == "usr_x"
    assert b["name"] == "usr_x_2"


def test_remove(tmp_path):
    s = UserFactorStore(root=tmp_path)
    s.add({"name": "usr_x", "family": "user", "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    assert s.remove("usr_x") is True
    with pytest.raises(KeyError):
        reg_get("usr_x")  # also evicted from the live registry
    assert s.list() == []
    assert s.remove("usr_x") is False


def test_missing_file_is_empty(tmp_path):
    s = UserFactorStore(root=tmp_path / "nope")
    assert s.list() == []
    assert s.register_all() == 0


def test_register_all_idempotent(tmp_path):
    s = UserFactorStore(root=tmp_path)
    s.add({"name": "usr_x", "family": "user", "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    # registering again (e.g. a 2nd startup in the same process) must replace, not raise
    assert s.register_all() == 1
    assert s.register_all() == 1
    assert reg_get("usr_x").family == "user"
