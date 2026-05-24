"""Tests for data/quote_fallback.py multi-source fallback chain.

v1.9.6: vibe-trading 借鉴的多源 fallback. 单源失败应自动 fallback 下一源,
全失败抛 RuntimeError + 每源原因明细 (诊断用).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

import pytest

from financial_analyst.data.quote_fallback import (
    fetch_realtime_quote,
    fetch_realtime_quotes,
    _is_valid_quote,
)


# ──────────────────────── _is_valid_quote ────────────────────────


def test_is_valid_quote_accepts_tencent_shape():
    assert _is_valid_quote({"name": "茅台", "price": 1290.2}) is True


def test_is_valid_quote_accepts_xueqiu_shape():
    """雪球字段叫 current 而非 price."""
    assert _is_valid_quote({"name": "茅台", "current": 1290.2}) is True


def test_is_valid_quote_rejects_empty():
    assert _is_valid_quote(None) is False
    assert _is_valid_quote({}) is False
    assert _is_valid_quote({"name": "no price field"}) is False


def test_is_valid_quote_rejects_none_price():
    assert _is_valid_quote({"name": "茅台", "price": None}) is False


# ──────────────────────── fetch_realtime_quote (single) ─────────


def test_first_source_succeeds_short_circuits():
    """tencent OK → xueqiu 不应被调."""
    calls = []

    def src1(code: str) -> Optional[Dict[str, Any]]:
        calls.append("tencent")
        return {"price": 100.0, "name": "T"}

    def src2(code: str) -> Optional[Dict[str, Any]]:
        calls.append("xueqiu")
        return {"current": 999.0}  # would override but should not be reached

    name, q = fetch_realtime_quote(
        "SH600519", sources=[("tencent", src1), ("xueqiu", src2)]
    )
    assert name == "tencent"
    assert q["price"] == 100.0
    assert calls == ["tencent"]  # xueqiu not called


def test_first_source_returns_none_falls_back():
    """tencent 返 None / empty → xueqiu 被调."""
    def src1(code: str): return None
    def src2(code: str): return {"current": 1290.2, "name": "茅台"}

    name, q = fetch_realtime_quote(
        "SH600519", sources=[("tencent", src1), ("xueqiu", src2)]
    )
    assert name == "xueqiu"
    assert q["current"] == 1290.2


def test_first_source_raises_falls_back():
    """tencent 抛异常 (网络挂) → xueqiu 被调, 不向上抛."""
    def src1(code: str):
        raise ConnectionError("tencent down")

    def src2(code: str):
        return {"current": 100, "name": "X"}

    name, q = fetch_realtime_quote(
        "SH600519", sources=[("tencent", src1), ("xueqiu", src2)]
    )
    assert name == "xueqiu"


def test_all_sources_fail_raises_with_details():
    """两源都挂 → RuntimeError, message 含每源原因."""
    def src1(code: str):
        raise ConnectionError("tencent network down")

    def src2(code: str):
        raise ValueError("xueqiu cookie expired")

    with pytest.raises(RuntimeError) as exc_info:
        fetch_realtime_quote(
            "SH600519", sources=[("tencent", src1), ("xueqiu", src2)]
        )
    msg = str(exc_info.value)
    assert "tencent" in msg
    assert "xueqiu" in msg
    assert "ConnectionError" in msg
    assert "ValueError" in msg
    assert "SH600519" in msg


def test_all_sources_empty_raises():
    """两源都返 empty (合法 response 但无 price) → RuntimeError."""
    def src1(code: str): return None
    def src2(code: str): return {}

    with pytest.raises(RuntimeError) as exc_info:
        fetch_realtime_quote(
            "SH600519", sources=[("tencent", src1), ("xueqiu", src2)]
        )
    msg = str(exc_info.value)
    assert "empty" in msg.lower() or "no-price" in msg


# ──────────────────────── fetch_realtime_quotes (batch) ─────────


def test_batch_first_source_succeeds():
    calls = []

    def src1(codes: List[str]) -> Dict[str, Dict[str, Any]]:
        calls.append("tencent")
        return {c: {"price": 100} for c in codes}

    def src2(codes: List[str]) -> Dict[str, Dict[str, Any]]:
        calls.append("xueqiu")
        return {}

    name, data = fetch_realtime_quotes(
        ["SH600519", "SZ300750"],
        sources=[("tencent", src1), ("xueqiu", src2)],
    )
    assert name == "tencent"
    assert len(data) == 2
    assert calls == ["tencent"]


def test_batch_first_source_empty_falls_back():
    """tencent 返 {} → xueqiu 被调."""
    def src1(codes: List[str]) -> Dict[str, Dict[str, Any]]:
        return {}  # 空 dict, 视为失败

    def src2(codes: List[str]) -> Dict[str, Dict[str, Any]]:
        return {c: {"price": 50} for c in codes}

    name, data = fetch_realtime_quotes(
        ["SH600519"], sources=[("tencent", src1), ("xueqiu", src2)]
    )
    assert name == "xueqiu"
    assert "SH600519" in data


def test_batch_first_source_raises_falls_back():
    def src1(codes: List[str]):
        raise TimeoutError("tencent timeout")

    def src2(codes: List[str]):
        return {"SH600519": {"price": 100}}

    name, data = fetch_realtime_quotes(
        ["SH600519"], sources=[("tencent", src1), ("xueqiu", src2)]
    )
    assert name == "xueqiu"


def test_batch_all_fail_raises_with_count():
    def src1(codes: List[str]):
        raise ConnectionError("down")

    def src2(codes: List[str]):
        return {}

    with pytest.raises(RuntimeError) as exc_info:
        fetch_realtime_quotes(
            ["SH600519", "SZ300750", "SZ000001"],
            sources=[("tencent", src1), ("xueqiu", src2)],
        )
    msg = str(exc_info.value)
    assert "3 codes" in msg  # 报告 batch size
    assert "ConnectionError" in msg


# ──────────────────────── default chains ────────────────────────


def test_default_single_chain_starts_with_tencent():
    """Default chain 顺序: tencent → xueqiu. tencent 在前是因为更快无 cookie."""
    from financial_analyst.data.quote_fallback import DEFAULT_SINGLE_SOURCES
    assert DEFAULT_SINGLE_SOURCES[0][0] == "tencent"
    assert DEFAULT_SINGLE_SOURCES[1][0] == "xueqiu"


def test_default_batch_chain_starts_with_tencent():
    from financial_analyst.data.quote_fallback import DEFAULT_BATCH_SOURCES
    assert DEFAULT_BATCH_SOURCES[0][0] == "tencent"
    assert DEFAULT_BATCH_SOURCES[1][0] == "xueqiu"
