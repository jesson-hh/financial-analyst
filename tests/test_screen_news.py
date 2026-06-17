# guanlan_v2.screen.news · C 真消息面(离线:东方财富代码映射 + prompt + 诚实护栏)
#
# 真快讯拉取/LLM 不入单测(外部网络);此处锁纯函数 + 不编造护栏。
from guanlan_v2.screen.news import _NEWS_SYSTEM, build_news_prompt, em_to_qlib


def test_em_to_qlib_maps_market_prefix():
    # 1=SH / 0=SZ;板块 BK 与其它市场(116.x)忽略
    codes = em_to_qlib("1.600030, 0.300750, 90.BK0800, 116.06030, 1.688981")
    assert codes == ["SH600030", "SZ300750", "SH688981"]
    assert em_to_qlib("") == []
    assert em_to_qlib(None) == []


def test_em_to_qlib_dedupes():
    assert em_to_qlib("1.600519, 1.600519") == ["SH600519"]


def test_build_news_prompt_market_and_codes():
    market = [{"time": "2026-06-06 14:40", "title": "证监会发布新规", "summary": "推动市场化"}]
    by_code = {"SH600519": [{"time": "2026-06-06 13:00", "title": "贵州茅台拟回购"}]}
    p = build_news_prompt(market, by_code)
    assert "证监会发布新规" in p
    assert "SH600519" in p and "贵州茅台拟回购" in p


def test_build_news_prompt_marks_no_news():
    p = build_news_prompt([{"time": "t", "title": "x", "summary": ""}], {})
    assert "无相关快讯" in p          # 诚实:无相关快讯明示,不编造


def test_news_system_forbids_fabrication():
    assert "不得编造" in _NEWS_SYSTEM
    assert "JSON" in _NEWS_SYSTEM
