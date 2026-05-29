"""Tests for the buddy ``factor_compose`` tool (_tool_factor_compose).

The tool wraps ``financial_analyst.factors.compose.compose_factors`` (which
loads a panel, fits a composite on TRAIN, evaluates it OOS, compares members).
compose_factors imports ``resolve_universe_codes`` + ``get_default_loader`` from
their home modules locally, so we monkeypatch THOSE with a stub loader (mirrors
tests/test_compose.py).

NOTE: we never call _clear_registry_for_tests (it would wipe the global alpha
registry and break cross-file tests). The tool only reads the registry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Importing the zoo package auto-registers alpha families (harmless; the tests
# below use pure expressions, mirroring the sibling compose test).
import financial_analyst.factors.zoo  # noqa: F401
from financial_analyst.buddy.tools import (
    TOOL_REGISTRY,
    ToolResult,
    _tool_factor_compose,
    get_tool,
)

CODES = ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750", "SH600276"]


# ---------------------------------------------------------------------------
# Stub loader: per-code random-walk close ~120 business days; no fundamentals.
# ---------------------------------------------------------------------------
def _random_walk_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame(
                {
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": np.full(len(dates), 1e6),
                },
                index=dates,
            )
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    return StubLoader()


def _patch(monkeypatch, loader, codes=CODES):
    # compose_factors imports these from their home modules (local imports),
    # so patch THOSE, not any buddy alias.
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes",
        lambda u: list(codes),
    )
    monkeypatch.setattr(
        "financial_analyst.data.loader_factory.get_default_loader",
        lambda: loader,
    )


# ---------------------------------------------------------------------------
# Happy path: 2 expressions + method="equal" → no error, report rendered.
# ---------------------------------------------------------------------------
def test_factor_compose_equal_ok(monkeypatch):
    _patch(monkeypatch, _random_walk_loader())
    res = _tool_factor_compose(
        members=["rank(-delta(close,5))", "rank(close)"],
        method="equal",
        universe="csi500",
        freq="week",
        train_frac=0.6,
    )
    assert isinstance(res, ToolResult)
    assert not res.is_error, f"unexpected error: {res.content}"
    # Method name (CN or raw) + the composite section present.
    assert ("综合" in res.content) or ("等权" in res.content) or ("equal" in res.content)
    # A verdict line is rendered (compose_factors verdict always has 'OOS Sharpe').
    assert "结论" in res.content
    assert "OOS Sharpe" in res.content
    # Member-comparison section lists both members.
    assert "rank(close)" in res.content
    assert "rank(-delta(close,5))" in res.content


# ---------------------------------------------------------------------------
# lgbm method also renders end-to-end on the stub panel.
# ---------------------------------------------------------------------------
def test_factor_compose_lgbm_ok(monkeypatch):
    _patch(monkeypatch, _random_walk_loader())
    res = _tool_factor_compose(
        members=["rank(-delta(close,5))", "rank(close)", "rank(ts_mean(returns,5))"],
        method="lgbm",
        universe="csi500",
        freq="week",
    )
    assert not res.is_error, f"unexpected error: {res.content}"
    assert ("LightGBM" in res.content) or ("lgbm" in res.content)
    assert "结论" in res.content


# ---------------------------------------------------------------------------
# members accepted as a ;/newline-delimited string (like alpha_compare).
# ---------------------------------------------------------------------------
def test_factor_compose_string_members(monkeypatch):
    _patch(monkeypatch, _random_walk_loader())
    res = _tool_factor_compose(
        members="rank(-delta(close,5)); rank(close)",
        method="equal",
        universe="csi500",
        freq="week",
    )
    assert not res.is_error, f"unexpected error: {res.content}"
    assert "结论" in res.content


# ---------------------------------------------------------------------------
# Fewer than 2 members → is_error (guard returns before any I/O).
# ---------------------------------------------------------------------------
def test_factor_compose_too_few_members():
    res = _tool_factor_compose(members=["rank(close)"], method="equal")
    assert isinstance(res, ToolResult)
    assert res.is_error
    assert "2" in res.content  # mentions the >=2 requirement


def test_factor_compose_empty_members():
    res = _tool_factor_compose(members=[], method="lgbm")
    assert res.is_error


# ---------------------------------------------------------------------------
# Empty universe → structured error surfaced as is_error (compose never raises).
# ---------------------------------------------------------------------------
def test_factor_compose_empty_universe(monkeypatch):
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes", lambda u: []
    )
    res = _tool_factor_compose(
        members=["rank(close)", "rank(-delta(close,5))"],
        method="equal",
        universe="nonexistent_xyz",
    )
    assert res.is_error
    assert "nonexistent_xyz" in res.content


# ---------------------------------------------------------------------------
# Tool is registered, well-formed, and confirm-gated (minutes-long).
# ---------------------------------------------------------------------------
def test_factor_compose_registered():
    assert "factor_compose" in {t.name for t in TOOL_REGISTRY}
    t = get_tool("factor_compose")
    assert t is not None
    assert t.confirm_required
    assert t.cost_hint == "minutes"
    schema = t.input_schema
    assert "members" in schema["properties"]
    assert schema["properties"]["members"]["type"] == "array"
    assert schema["required"] == []  # SP-D.2: members 或 goal 二选一 (运行时校验, 非 schema 强制)
    assert "goal" in schema["properties"]  # LLM 配方入口
    assert schema["properties"]["method"]["enum"] == ["equal", "ic_weighted", "linear", "lgbm"]
    assert schema["properties"]["method"]["default"] == "lgbm"
    # OpenAI + Anthropic schema render without error.
    assert t.to_anthropic_schema()["name"] == "factor_compose"
    assert t.to_openai_schema()["function"]["name"] == "factor_compose"
