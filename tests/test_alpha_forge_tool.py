from __future__ import annotations
import numpy as np
import pandas as pd
import pytest


def _stub_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2024-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                               "close": close, "volume": np.full(len(dates), 1e6)}, index=dates)
            df.index.name = "datetime"
            return df
    return StubLoader()


def _ok_forge(name="usr_rev5", expr="rank(-delta(close,5))"):
    from financial_analyst.factors.forge import ForgeResult
    return ForgeResult(idea="5日反转", expr=expr, parsed=[{"k": "方向", "v": "反转"}],
                       name=name, rationale="5日反转", compile_ok=True)


def test_alpha_forge_runs_no_save(monkeypatch):
    from financial_analyst.buddy import tools as t
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor", lambda idea, **k: _ok_forge())
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes",
                        lambda u: ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"])
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader", lambda: _stub_loader())
    res = t._tool_alpha_forge(idea="5日反转", save=False, universe="csi500")
    assert res.is_error is False
    assert "rank(-delta(close,5))" in res.content
    assert "RankIC" in res.content


def test_alpha_forge_out_of_vocab_is_error(monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.forge import ForgeResult
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor",
                        lambda idea, **k: ForgeResult(idea=idea, out_of_vocab=True, compile_ok=False,
                                                       error="需要 dv_ttm 基本面字段"))
    res = t._tool_alpha_forge(idea="高股息", save=False)
    assert res.is_error is True
    assert "dv_ttm" in res.content or "基本面" in res.content


def test_alpha_forge_save_registers(tmp_path, monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.zoo.registry import get as reg_get
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor", lambda idea, **k: _ok_forge(name="usr_saved"))
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes",
                        lambda u: ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"])
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader", lambda: _stub_loader())
    res = t._tool_alpha_forge(idea="5日反转", save=True, universe="csi500")
    assert res.is_error is False
    assert reg_get("usr_saved").family == "user"


def test_user_factors_lists(tmp_path, monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.forge import UserFactorStore
    UserFactorStore(root=tmp_path / "factors").add({"name": "usr_a", "family": "user",
        "expr": "rank(close)", "description": "d", "parsed": [], "kpis": {}})
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    res = t._tool_user_factors()
    assert res.is_error is False
    assert "usr_a" in res.content


def test_forge_and_user_factors_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    names = {x.name for x in TOOL_REGISTRY}
    assert "alpha_forge" in names and "user_factors" in names


def test_user_factors_remove(tmp_path, monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.forge import UserFactorStore
    UserFactorStore(root=tmp_path / "factors").add({"name": "usr_del", "family": "user",
        "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    res = t._tool_user_factors(remove="usr_del")
    assert res.is_error is False
    assert "已删除" in res.content
    assert t._tool_user_factors().content  # list path still works (now empty)
