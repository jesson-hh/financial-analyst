"""Tests for v1.9.2 Tencent batch quote collector + batch alert eval."""
from __future__ import annotations
from unittest.mock import patch

import pytest

from financial_analyst.data.collectors.tencent_quote import (
    TencentQuoteCollector, _to_tencent, _norm, _f,
)

# A real-ish Tencent payload (2 stocks), GBK-decoded form.
_SAMPLE = (
    'v_sh600519="1~贵州茅台~600519~1311.00~1315.00~1312.98~38868~17258~21610'
    '~1311.00~198~1310.99~6~1310.96~5~1310.91~1~1310.90~2~1311.01~5~1311.03~2'
    '~1311.04~2~1311.05~5~1311.06~2~~20260521161407~-4.00~-0.30~1323.52~1311.00'
    '~1311.00/38868/5112740391~38868~511274~0.31~19.85~~1323.52~1311.00~0.95'
    '~16417.26~16417.26~6.06~1446.50~1183.50~0.77~196~1315.40~15.07~19.94~~~0.42'
    '~511274.0391~0.0000~0~ ~GP-A~-4.81~-2.32~3.94~30.53~26.78~1568.00~1311.00'
    '~-4.38~-7.08~-11.81~1252270215~1252270215~85.96~-6.66~1252270215";\n'
    'v_sz300750="51~宁德时代~300750~411.63~414.75~411.63~338671~0~0~411.63~1'
    '~411.62~6~411.60~5~411.55~1~411.50~2~411.64~5~411.66~2~411.70~2~411.75~5'
    '~411.80~2~~20260521161407~-3.12~-0.75~427.62~411.42~411.63/338671/13900000000'
    '~338671~1390000~1.15~24.11~~427.62~411.42~3.91~9044.10~19044.10~5.20~452.00'
    '~370.00~1.15~100~414.00~22.00~24.00~~~0.50~1390000~0~0~ ~GP-A~-5~-5~5~30~26'
    '~450~410~-5~-8~-12~462000000~462000000~80~-5~462000000";\n'
)


# ----- code conversion ------------------------------------------------------


def test_to_tencent():
    assert _to_tencent("SH600519") == "sh600519"
    assert _to_tencent("600519") == "sh600519"
    assert _to_tencent("300750") == "sz300750"
    assert _to_tencent("sz000858") == "sz000858"
    assert _to_tencent("300750.SZ") == "sz300750"


def test_norm():
    assert _norm("sh600519") == "SH600519"
    assert _norm("SZ300750") == "SZ300750"


def test_f():
    assert _f("1.5") == 1.5
    assert _f("") is None
    assert _f("abc") is None


# ----- parsing --------------------------------------------------------------


def test_parse_extracts_fields():
    d = TencentQuoteCollector._parse(_SAMPLE)
    assert "SH600519" in d and "SZ300750" in d
    mt = d["SH600519"]
    assert mt["name"] == "贵州茅台"
    assert mt["price"] == 1311.0
    assert mt["changePercent"] == -0.30
    assert mt["pe"] == 19.85
    assert mt["pb"] == 6.06
    assert mt["vol_ratio"] == 0.77
    assert mt["total_mv"] == 16417.26
    assert mt["turnover_rate"] == 0.31


def test_fetch_adds_input_aliases():
    """fetch(['600519']) should let you look it up by '600519' too."""
    with patch.object(TencentQuoteCollector, "_parse",
                      return_value={"SH600519": {"code": "SH600519", "price": 1311.0}}):
        with patch("httpx.Client") as MockClient:
            inst = MockClient.return_value.__enter__.return_value
            inst.get.return_value.content = b""
            inst.get.return_value.raise_for_status = lambda: None
            d = TencentQuoteCollector().fetch(["600519"])
    assert "SH600519" in d
    assert "600519" in d  # alias
    assert d["600519"]["price"] == 1311.0


def test_fetch_empty_codes():
    assert TencentQuoteCollector().fetch([]) == {}


def test_quote_single():
    with patch.object(TencentQuoteCollector, "fetch",
                      return_value={"SH600519": {"code": "SH600519", "price": 1311.0}}):
        q = TencentQuoteCollector().quote("SH600519")
    assert q["price"] == 1311.0


# ----- batch alert eval -----------------------------------------------------


def test_evaluate_batch_one_fetch_for_all(tmp_path):
    from financial_analyst.buddy.alerts import AlertStore, evaluate_batch
    store = AlertStore(path=tmp_path / "a.yaml")
    store.add("SH600519", "price_below", 1400)   # triggers (1311 < 1400)
    store.add("SZ300750", "price_above", 999)    # no (411 < 999)
    store.add("SH600519", "price_above", 9999)   # no (1311 < 9999)

    calls = []

    def batch(codes):
        calls.append(list(codes))
        return {
            "SH600519": {"price": 1311.0, "changePercent": -0.3},
            "SZ300750": {"price": 411.6, "changePercent": -0.75},
        }

    fired = evaluate_batch(store, batch)
    # ONE batch call for all distinct codes
    assert len(calls) == 1
    assert set(calls[0]) == {"SH600519", "SZ300750"}
    # only the price_below 1400 fires
    assert len(fired) == 1
    assert fired[0][0].kind == "price_below"


def test_evaluate_batch_handles_missing_quote(tmp_path):
    from financial_analyst.buddy.alerts import AlertStore, evaluate_batch
    store = AlertStore(path=tmp_path / "a.yaml")
    store.add("SH600519", "price_below", 1400)
    fired = evaluate_batch(store, lambda codes: {})  # provider returns nothing
    assert fired == []


def test_quote_batch_tool_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    assert "quote_batch" in {t.name for t in TOOL_REGISTRY}


def test_quote_batch_tool_formats(tmp_path):
    from financial_analyst.buddy import tools
    fake = {
        "SH600519": {"code": "SH600519", "name": "贵州茅台", "price": 1311.0,
                     "changePercent": -0.3, "vol_ratio": 0.77,
                     "turnover_rate": 0.31, "pe": 19.85},
    }
    with patch("financial_analyst.data.collectors.tencent_quote."
               "TencentQuoteCollector.fetch", return_value=fake):
        r = tools._tool_quote_batch("SH600519")
    assert not r.is_error
    assert "贵州茅台" in r.content
    assert "-0.30%" in r.content
    assert r.side_effect["quotes"]["SH600519"]["pe"] == 19.85
