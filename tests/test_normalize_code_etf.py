from financial_analyst.buddy.tools import normalize_code


def test_bare_etf_codes_get_prefix():
    assert normalize_code("510300") == "SH510300"
    assert normalize_code("159915") == "SZ159915"
    assert normalize_code("588000") == "SH588000"
    assert normalize_code("512880") == "SH512880"


def test_prefixed_and_suffixed_etf_still_work():
    assert normalize_code("SH510300") == "SH510300"
    assert normalize_code("510300.SH") == "SH510300"
    assert normalize_code("sz159915") == "SZ159915"


def test_stock_codes_unchanged():
    assert normalize_code("600519") == "SH600519"
    assert normalize_code("000001") == "SZ000001"
    assert normalize_code("300750") == "SZ300750"
    assert normalize_code("430017") == "BJ430017"


def test_bond_not_misclassified():
    assert normalize_code("110059") == "110059"
    assert normalize_code("123120") == "123120"
