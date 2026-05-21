"""Tests for XueqiuFeedCollector + XueqiuHotPostsCollector + mention parsing."""
from __future__ import annotations
from unittest.mock import patch

from financial_analyst.data.collectors.opencli.xueqiu_feed import (
    XueqiuFeedCollector, _extract_mentions,
)
from financial_analyst.data.collectors.opencli.xueqiu_hot_posts import (
    XueqiuHotPostsCollector,
)


# ----- cashtag / mention parsing --------------------------------------------


def test_extract_cashtag_a_share():
    assert _extract_mentions("$贵州茅台(SH600519)$ 最近怎么样") == "SH600519"


def test_extract_cashtag_hk():
    assert _extract_mentions("$腾讯控股(00700)$ 财报") == "00700"


def test_extract_multiple_unique_mentions():
    text = "$茅台(SH600519)$ 和 $五粮液(SZ000858)$ 都涨, 但 $茅台(SH600519)$ 更猛"
    assert _extract_mentions(text) == "SH600519,SZ000858"


def test_extract_falls_back_to_paren_only():
    """When $...$ is missing but (CODE) is present in author/text."""
    assert _extract_mentions("万华化学(SH600309)") == "SH600309"


def test_extract_empty_when_no_mention():
    assert _extract_mentions("纯观点贴, 没有任何股票代码") == ""


def test_extract_combines_text_and_author():
    """Author label often carries the code; we should sniff both."""
    out = _extract_mentions("看好半导体", "腾讯控股(00700)")
    assert out == "00700"


# ----- feed collector --------------------------------------------------------


def test_feed_normalises_to_news_shape():
    fake = [
        {"author": "万华化学(SH600309)", "text": "5月21日消息...专利公告",
         "likes": 0, "replies": 0,
         "url": "https://xueqiu.com/null/390065800"},
        {"author": "陆家嘴幽灵", "text": "$腾讯(00700)$ 财报点评",
         "likes": 13, "replies": 1,
         "url": "https://xueqiu.com/u/x"},
    ]
    with patch("financial_analyst.data.collectors.opencli.xueqiu_feed.run_opencli",
               return_value=fake):
        out = XueqiuFeedCollector().fetch(limit=5)

    assert len(out) == 2
    # Keys expected by NewsDB.upsert_news
    for item in out:
        assert "time" in item
        assert "title" in item
        assert "content" in item
        assert "url" in item
        assert "stocks" in item
    # Mention parsing worked
    assert "SH600309" in out[0]["stocks"]
    assert "00700" in out[1]["stocks"]


def test_feed_passes_pagination_args():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_feed.run_opencli",
               return_value=[]) as mock:
        XueqiuFeedCollector().fetch(limit=15, page=3)
    args = mock.call_args.args
    assert "xueqiu" in args and "feed" in args
    assert "15" in args
    assert "3" in args


def test_feed_handles_empty():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_feed.run_opencli",
               return_value=[]):
        assert XueqiuFeedCollector().fetch() == []


def test_feed_title_truncates_long_text():
    """A 200-char post should yield an 80-char + ellipsis title."""
    long_text = "x" * 200
    fake = [{"author": "a", "text": long_text, "url": "u"}]
    with patch("financial_analyst.data.collectors.opencli.xueqiu_feed.run_opencli",
               return_value=fake):
        out = XueqiuFeedCollector().fetch()
    assert len(out[0]["title"]) == 81  # 80 chars + "…"
    assert out[0]["title"].endswith("…")


# ----- hot-posts collector ---------------------------------------------------


def test_hot_posts_normalises_to_news_shape():
    fake = [
        {"rank": 1, "author": "陆家嘴幽灵",
         "text": "英伟达Q1业绩超预期",
         "likes": 13, "url": "https://xueqiu.com/x/y"},
        {"rank": 2, "author": "大V",
         "text": "$中矿资源(SZ002738)$ 等待澳大利亚勘测",
         "likes": 9, "url": "https://xueqiu.com/a/b"},
    ]
    with patch("financial_analyst.data.collectors.opencli.xueqiu_hot_posts.run_opencli",
               return_value=fake):
        out = XueqiuHotPostsCollector().fetch(limit=5)
    assert len(out) == 2
    assert out[1]["stocks"] == "SZ002738"
    assert all("title" in it and "content" in it for it in out)


def test_hot_posts_passes_limit():
    with patch("financial_analyst.data.collectors.opencli.xueqiu_hot_posts.run_opencli",
               return_value=[]) as mock:
        XueqiuHotPostsCollector().fetch(limit=42)
    assert "42" in mock.call_args.args
