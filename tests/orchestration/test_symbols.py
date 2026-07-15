from __future__ import annotations
import pytest
from guanlan_v2.orchestration.data.symbols import Symbol, InstrumentMeta, LimitRule


def test_symbol_dotted_and_engine_code():
    s = Symbol(code="600519", exchange="SH", board="main")
    assert s.dotted == "600519.SH"
    assert s.engine_code == "SH600519"


def test_symbol_rejects_non_six_digit_code():
    with pytest.raises(ValueError):
        Symbol(code="60051", exchange="SH", board="main")
    with pytest.raises(ValueError):
        Symbol(code="60051X", exchange="SH", board="main")


def test_symbol_is_frozen():
    s = Symbol(code="600519", exchange="SH", board="main")
    with pytest.raises(Exception):
        s.code = "000001"  # frozen


def test_instrument_meta_is_st_unknown_defaults_none():
    m = InstrumentMeta(symbol=Symbol(code="600519", exchange="SH", board="main"))
    assert m.is_st is None  # cannot infer from code; unknown must be explicit


def test_limit_rule_allows_none_pct():
    r = LimitRule(pct=None, reason="rule unknown", rule_version="v0")
    assert r.pct is None


def test_symbol_rejects_exchange_board_mismatch():
    with pytest.raises(ValueError):
        Symbol(code="688001", exchange="SZ", board="star")


def test_limit_rule_rejects_invalid_pct():
    with pytest.raises(ValueError):
        LimitRule(pct=-0.1, reason="bad", rule_version="v1")
    with pytest.raises(ValueError):
        LimitRule(pct=float("inf"), reason="bad", rule_version="v1")
