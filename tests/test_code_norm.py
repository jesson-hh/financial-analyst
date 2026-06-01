from financial_analyst.data.code_norm import etf_exchange


def test_shanghai_etf_prefixes():
    assert etf_exchange("510300") == "SH"
    assert etf_exchange("512880") == "SH"
    assert etf_exchange("560000") == "SH"
    assert etf_exchange("588000") == "SH"


def test_shenzhen_etf_prefixes():
    assert etf_exchange("159915") == "SZ"
    assert etf_exchange("159919") == "SZ"


def test_non_etf_returns_none():
    assert etf_exchange("600519") is None
    assert etf_exchange("000001") is None
    assert etf_exchange("300750") is None
    assert etf_exchange("430017") is None
    assert etf_exchange("110059") is None   # SH convertible bond — NOT ETF
    assert etf_exchange("123120") is None   # SZ convertible bond — NOT ETF


def test_malformed_returns_none():
    assert etf_exchange("51030") is None
    assert etf_exchange("5103000") is None
    assert etf_exchange("SH510300") is None
    assert etf_exchange("") is None
