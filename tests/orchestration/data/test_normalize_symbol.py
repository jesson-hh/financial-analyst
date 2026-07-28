"""Tests for :func:`normalize_symbol` — purely syntactic, path-safe A-share
symbol normalization (Phase 3, Task 2).

The function is normative: it accepts only the *complete* bare / dotted / engine
grammar, infers exchange+board from the 号段, and rejects embedded codes,
non-strings, multiple codes, and explicit-exchange conflicts rather than
silently repairing them. The six-digit ``code`` it returns is the value that may
later become a cache key, so its shape (``^[0-9]{6}$``) is load-bearing.
"""
from __future__ import annotations

import re

import pytest

from guanlan_v2.orchestration.data.symbols import Symbol, normalize_symbol

_SIX_DIGITS = re.compile(r"^[0-9]{6}$")


# ── normative cases (from the task brief) ──────────────────────────────────
def test_bare_dotted_engine_all_normalize():
    for raw in ["600519", "600519.SH", "SH600519", "sh600519"]:
        s = normalize_symbol(raw)
        assert s.code == "600519" and s.exchange == "SH" and s.board == "main"


def test_star_board():
    s = normalize_symbol("688981")
    assert s.exchange == "SH" and s.board == "star"


def test_chinext_board():
    s = normalize_symbol("300750")
    assert s.exchange == "SZ" and s.board == "chinext"


def test_bj_board():
    s = normalize_symbol("830799")
    assert s.exchange == "BJ" and s.board == "bj"


def test_bj_920_board():
    # 北交所新号段(920xxx):三种完整写法都必须归一到 BJ/bj,而不是落穿到 SZ/main。
    for raw in ["920807", "920807.BJ", "BJ920807", "bj920807"]:
        s = normalize_symbol(raw)
        assert s.code == "920807" and s.exchange == "BJ" and s.board == "bj"


def test_sz_main():
    assert normalize_symbol("000001").exchange == "SZ"


def test_rejects_no_six_digit_code():
    with pytest.raises(ValueError):
        normalize_symbol("AAPL")
    with pytest.raises(ValueError):
        normalize_symbol("60051")


@pytest.mark.parametrize("raw", ["x600519", "600519-extra", "600519 000001", 600519])
def test_rejects_partial_or_coerced_input(raw):
    with pytest.raises((TypeError, ValueError)):
        normalize_symbol(raw)


@pytest.mark.parametrize("raw", ["600519.SZ", "SZ600519"])
def test_rejects_explicit_exchange_conflict(raw):
    with pytest.raises(ValueError, match="exchange"):
        normalize_symbol(raw)


# ── extended coverage (must stay consistent with the normative impl) ────────
def test_returns_symbol_instance():
    assert isinstance(normalize_symbol("600519"), Symbol)


@pytest.mark.parametrize(
    "raw,code,exchange,board",
    [
        ("301000", "301000", "SZ", "chinext"),  # 301 also 创业板
        ("000001", "000001", "SZ", "main"),
        ("002594", "002594", "SZ", "main"),  # 中小板 folds into SZ main
        ("600000", "600000", "SH", "main"),
        ("688981", "688981", "SH", "star"),
        ("430047", "430047", "BJ", "bj"),  # leading 4 → 北交所
        ("830799", "830799", "BJ", "bj"),  # leading 8 → 北交所
        ("870508", "870508", "BJ", "bj"),
        ("880001", "880001", "BJ", "bj"),  # 88 covered by leading 8
        ("920807", "920807", "BJ", "bj"),  # 北交所 920 新号段
        ("921000", "921000", "SZ", "main"),  # only exact 920 maps BJ; 92x stays fall-through
        ("900001", "900001", "SZ", "main"),  # other leading-9 unchanged (documented fall-through)
    ],
)
def test_board_inference_by_prefix(raw, code, exchange, board):
    s = normalize_symbol(raw)
    assert s.code == code and s.exchange == exchange and s.board == board


@pytest.mark.parametrize(
    "raw",
    ["300750", "300750.SZ", "SZ300750", "sz300750", "  300750  ", "300750.sz"],
)
def test_equivalent_forms_agree(raw):
    """All complete forms of one instrument collapse to the same Symbol."""
    s = normalize_symbol(raw)
    assert s.code == "300750" and s.exchange == "SZ" and s.board == "chinext"


def test_dotted_and_engine_matching_exchange_accepted():
    assert normalize_symbol("688981.SH").board == "star"
    assert normalize_symbol("SH688981").board == "star"
    assert normalize_symbol("830799.BJ").exchange == "BJ"
    assert normalize_symbol("BJ430047").exchange == "BJ"
    assert normalize_symbol("920807.BJ").exchange == "BJ"
    assert normalize_symbol("BJ920807").exchange == "BJ"


@pytest.mark.parametrize("raw", ["920807.SZ", "SZ920807", "920807.SH", "SH920807"])
def test_bj_920_explicit_exchange_conflict_rejected(raw):
    with pytest.raises(ValueError, match="exchange"):
        normalize_symbol(raw)


def test_code_is_always_six_digit_cache_key_safe():
    """The returned code must match ^[0-9]{6}$ before it may key a cache."""
    for raw in ["600519", "000001", "688981.SH", "SZ300750", "830799"]:
        assert _SIX_DIGITS.fullmatch(normalize_symbol(raw).code)


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty
        "   ",  # whitespace only
        "6005190",  # 7 digits, no match
        "60051",  # 5 digits
        "600519.SHH",  # bad exchange token
        "600519.US",  # unknown exchange
        "US600519",  # unknown exchange prefix
        "600519SH",  # engine grammar is prefix-only
        "600519.SH.SZ",  # trailing junk
        "600519\n000001",  # embedded second code via newline
        ".600519",  # leading dot
        "600519.",  # trailing dot
    ],
)
def test_rejects_bad_grammar(raw):
    with pytest.raises(ValueError):
        normalize_symbol(raw)


@pytest.mark.parametrize("raw", [600519, None, 6.0, True, b"600519", ["600519"]])
def test_rejects_non_string(raw):
    with pytest.raises(TypeError):
        normalize_symbol(raw)


@pytest.mark.parametrize("raw", ["000001.SH", "SH000001", "688981.SZ", "SZ688981"])
def test_conflict_message_names_exchange(raw):
    with pytest.raises(ValueError, match="exchange"):
        normalize_symbol(raw)


def test_bj_dotted_conflict_rejected():
    # a 6-code with an explicit .BJ is a conflict (6 → SH main)
    with pytest.raises(ValueError, match="exchange"):
        normalize_symbol("600519.BJ")
