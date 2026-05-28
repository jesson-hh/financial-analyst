from __future__ import annotations
import numpy as np
import pandas as pd
import pytest


def _stub_panel_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame({
                "open": close, "high": close * 1.01, "low": close * 0.99,
                "close": close, "volume": np.full(len(dates), 1e6),
            }, index=dates)
            df.index.name = "datetime"
            return df
    return StubLoader()


def test_factor_report_tool_runs(monkeypatch):
    from financial_analyst.buddy import tools as t
    # NOTE: engine's factor_report() imports resolve_universe_codes + get_default_loader
    # from their home modules (local imports), so patch THOSE, not buddy's aliases.
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes",
                        lambda u: ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"])
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader",
                        lambda: _stub_panel_loader())

    res = t._tool_factor_report(expr_or_name="rank(-delta(close,5))", universe="csi500", freq="week")
    assert res.is_error is False
    assert "RankIC" in res.content
    assert "Sharpe" in res.content


def test_factor_report_tool_bad_expr(monkeypatch):
    from financial_analyst.buddy import tools as t
    res = t._tool_factor_report(expr_or_name="import os", universe="csi500", freq="week")
    assert res.is_error is True


def test_factor_report_tool_empty_universe(monkeypatch):
    """Non-ok status (empty universe) → is_error with the status surfaced."""
    from financial_analyst.buddy import tools as t
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes", lambda u: [])
    res = t._tool_factor_report(expr_or_name="rank(-delta(close,5))", universe="nonexistent_xyz", freq="week")
    assert res.is_error is True
    assert ("empty_universe" in res.content) or ("解析为空" in res.content)


def test_factor_report_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    names = {tool.name for tool in TOOL_REGISTRY}
    assert "factor_report" in names
