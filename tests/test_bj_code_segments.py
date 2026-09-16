"""北交所旧号段与 920 新号段的共享归一化回归。"""
from financial_analyst.buddy.tools import normalize_code
from financial_analyst.data import f10_corpus as fc
from financial_analyst.data import stock_basic as sb
from financial_analyst.data.code_norm import is_bj_code
from financial_analyst.data.collectors.opencli.xueqiu_comments import _normalize_symbol
from financial_analyst.data.collectors.tencent_quote import _to_tencent
from financial_analyst.data.updaters.f10 import _prefix_code
from financial_analyst.data.updaters.pytdx_pool import qlib_code_to_pytdx


def test_is_bj_code_segments_are_narrow():
    for code in ("430047", "832000", "870001", "889000", "920002", "920999"):
        assert is_bj_code(code), code
    for code in (
        "600519", "000630", "300750", "688981", "510300", "159915",
        "900901", "921000", "92", "9200020", "abc920", "", None,
    ):
        assert not is_bj_code(code), code


def test_f10_normalizes_bj_and_preserves_non_bj_segments():
    assert fc._norm_code("920002") == "BJ920002"
    assert fc._norm_code("430047") == "BJ430047"
    assert fc._norm_code("832000") == "BJ832000"
    assert fc._norm_code("600519") == "SH600519"
    assert fc._norm_code("900901") == "SH900901"
    assert fc._norm_code("510300") == "SH510300"
    assert fc._norm_code("000630") == "SZ000630"


def test_f10_locate_uses_bj_directory(tmp_path):
    directory = tmp_path / "bj920002"
    directory.mkdir()
    (directory / "最新提示_20260725.txt").write_text("x", encoding="utf-8")
    hit = fc.locate("920002", root=tmp_path)
    assert hit is not None and "最新提示" in hit


def test_stock_basic_normalizes_bj_ts_codes():
    assert sb._normalize_ts_code("920002") == "920002.BJ"
    assert sb._normalize_ts_code("430047") == "430047.BJ"
    assert sb._normalize_ts_code("832000") == "832000.BJ"
    assert sb._normalize_ts_code("BJ920002") == "920002.BJ"
    assert sb._normalize_ts_code("600519") == "600519.SH"
    assert sb._normalize_ts_code("000630") == "000630.SZ"


def test_pytdx_maps_bj_segments_to_market_two():
    assert qlib_code_to_pytdx("920002") == (2, "920002")
    assert qlib_code_to_pytdx("BJ920002") == (2, "920002")
    assert qlib_code_to_pytdx("430047") == (2, "430047")
    assert qlib_code_to_pytdx("832000") == (2, "832000")
    assert qlib_code_to_pytdx("600519") == (1, "600519")
    assert qlib_code_to_pytdx("000630") == (0, "000630")


def test_f10_updater_prefixes_bj_segments():
    assert _prefix_code("920002") == "BJ920002"
    assert _prefix_code("430047") == "BJ430047"
    assert _prefix_code("920002.BJ") == "BJ920002"
    assert _prefix_code("600000") == "SH600000"
    assert _prefix_code("000001") == "SZ000001"


def test_external_symbol_normalizers_share_the_bj_rule():
    assert _to_tencent("920002") == "bj920002"
    assert _to_tencent("430047") == "bj430047"
    assert _to_tencent("832000") == "bj832000"
    assert _to_tencent("900901") == "900901"  # existing fallback; never misclassify as BJ
    assert _normalize_symbol("920002") == "BJ920002"
    assert _normalize_symbol("430047") == "BJ430047"
    assert normalize_code("920002") == "BJ920002"
    assert normalize_code("430047") == "BJ430047"
    assert normalize_code("920002.BJ") == "BJ920002"
