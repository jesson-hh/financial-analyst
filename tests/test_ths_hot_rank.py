"""Tests for THSHotRankCollector (opencli adapter for 同花顺热股榜)."""
from __future__ import annotations
from unittest.mock import patch

import pytest

from financial_analyst.data.collectors.opencli.ths_hot_rank import (
    THSHotRankCollector, _extract_code,
)


# ----- code extraction helper -----------------------------------------------


def test_extract_code_from_leading_digits():
    """Tags like '002342,商业航天,军工' lead with the 6-digit code."""
    assert _extract_code("002342,商业航天,军工") == "002342"
    assert _extract_code("000066,中国AI 50,AI PC") == "000066"


def test_extract_code_returns_empty_when_absent():
    """Tags like '持续上榜,绿色电力,风电' have no embedded code."""
    assert _extract_code("持续上榜,绿色电力,风电") == ""
    assert _extract_code("") == ""
    assert _extract_code("首板涨停,MicroLED概念") == ""


def test_extract_code_handles_leading_whitespace():
    """opencli output sometimes has slight whitespace inconsistencies."""
    assert _extract_code("  002342, foo") == "002342"


def test_extract_code_doesnt_match_partial_digits():
    """Don't pull '12345' (only 5 digits) or '1234567' (7 digits)."""
    assert _extract_code("12345,short") == ""
    # 7+ digits: regex matches first 6 then requires \b — '1234567' has no
    # word boundary after position 6 since position 6 is also a digit.
    assert _extract_code("1234567,long") == ""


# ----- collector contract ---------------------------------------------------


def test_collector_normalises_opencli_output():
    """fetch() must transform opencli rows into the {rank, code, name,
    changePercent, heat, tags} shape that NewsDB.upsert_hot_stocks expects."""
    fake_raw = [
        {"rank": "1", "name": "大唐发电", "changePercent": "-1.99%",
         "heat": "686.7万热度", "tags": "持续上榜,绿色电力,风电"},
        {"rank": "4", "name": "巨力索具", "changePercent": "-9.99%",
         "heat": "484.9万热度", "tags": "002342,商业航天,军工"},
    ]
    with patch("financial_analyst.data.collectors.opencli.ths_hot_rank.run_opencli",
               return_value=fake_raw):
        out = THSHotRankCollector().fetch(limit=5)

    assert len(out) == 2
    # Row 1: no embedded code
    assert out[0]["rank"] == "1"
    assert out[0]["code"] == ""
    assert out[0]["name"] == "大唐发电"
    assert out[0]["changePercent"] == "-1.99%"
    assert out[0]["heat"] == "686.7万热度"
    assert out[0]["tags"] == "持续上榜,绿色电力,风电"
    # Row 2: code extracted from tags
    assert out[1]["code"] == "002342"
    assert out[1]["name"] == "巨力索具"


def test_collector_passes_limit_to_opencli():
    """The limit parameter must reach run_opencli."""
    with patch("financial_analyst.data.collectors.opencli.ths_hot_rank.run_opencli",
               return_value=[]) as mock:
        THSHotRankCollector().fetch(limit=37)
    args = mock.call_args.args
    assert "ths" in args and "hot-rank" in args
    # find limit value
    assert "37" in args


def test_collector_handles_empty_opencli_response():
    """opencli may return [] for a temporarily-empty board."""
    with patch("financial_analyst.data.collectors.opencli.ths_hot_rank.run_opencli",
               return_value=[]):
        assert THSHotRankCollector().fetch() == []


def test_collector_handles_none_opencli_response():
    """Defensive: opencli wrapper might return None on edge cases."""
    with patch("financial_analyst.data.collectors.opencli.ths_hot_rank.run_opencli",
               return_value=None):
        assert THSHotRankCollector().fetch() == []


# ----- integration with NewsDB upsert ---------------------------------------


def test_collector_output_satisfies_upsert_hot_stocks_schema():
    """upsert_hot_stocks reads {rank, code/symbol, name, price, changePercent, heat}.
    Verify our collector's output has all the expected keys.

    Note: 用 limit=999 (而非 default 20) 绕开 net.py @rate_limited cache —
    cache key 含 limit, 用 unique 值确保 mock 真生效, 不会命中其他 test 跑过的 cached entry.
    """
    fake_raw = [{"rank": "1", "name": "test", "changePercent": "+1%",
                 "heat": "100万", "tags": "000001,foo"}]
    with patch("financial_analyst.data.collectors.opencli.ths_hot_rank.run_opencli",
               return_value=fake_raw):
        items = THSHotRankCollector().fetch(limit=999)
    item = items[0]
    # Keys upsert_hot_stocks reads from the dict
    assert "rank" in item
    assert "code" in item or "symbol" in item  # either is accepted
    assert "name" in item
    assert "changePercent" in item
    assert "heat" in item
