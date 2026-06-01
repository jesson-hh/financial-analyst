from financial_analyst.data.collectors.tencent_quote import _to_tencent


def test_bare_etf_codes_get_tencent_prefix():
    assert _to_tencent("510300") == "sh510300"
    assert _to_tencent("159915") == "sz159915"
    assert _to_tencent("588000") == "sh588000"


def test_prefixed_and_suffixed_etf_still_work():
    assert _to_tencent("SH510300") == "sh510300"
    assert _to_tencent("510300.SH") == "sh510300"


def test_stock_codes_unchanged():
    assert _to_tencent("600519") == "sh600519"
    assert _to_tencent("000001") == "sz000001"
    assert _to_tencent("430017") == "bj430017"
