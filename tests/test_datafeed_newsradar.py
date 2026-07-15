# -*- coding: utf-8 -*-
"""datafeed.newsradar 单测(全离线):RSS/Atom 解析 + 源表加载 + 聚合(桩 fetch)。
自写 fetcher/parser,不沿用 vibe 的 redline 词表与「先截断后过滤」顺序 bug。"""
import guanlan_v2.datafeed.newsradar as nr


def test_parse_feed_rss2_strips_html_and_maps_fields():
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Big AI news</title><link>https://x.com/a</link>
        <description>&lt;p&gt;summary here&lt;/p&gt;</description>
        <pubDate>Wed, 15 Jul 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    items = nr._parse_feed(xml, "OpenAI", "ai")
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "Big AI news"
    assert it["url"] == "https://x.com/a"
    assert it["summary"] == "summary here"          # HTML 标签剥离
    assert it["source"] == "OpenAI" and it["sector"] == "ai"
    assert it["ts"] > 0                              # pubDate 解析出 epoch


def test_parse_feed_atom_uses_link_href_and_summary():
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Chip advance</title>
        <link href="https://y.com/b"/>
        <summary>atom summary</summary>
        <updated>2026-07-15T09:00:00Z</updated></entry>
    </feed>"""
    items = nr._parse_feed(xml, "DIGITIMES", "semi")
    assert len(items) == 1
    assert items[0]["title"] == "Chip advance"
    assert items[0]["url"] == "https://y.com/b"      # Atom link 取 href 属性
    assert items[0]["summary"] == "atom summary"
    assert items[0]["sector"] == "semi"


def test_parse_feed_skips_entries_without_title():
    xml = """<?xml version="1.0"?><rss><channel>
      <item><link>https://x.com/notitle</link></item>
      <item><title>Has title</title><link>https://x.com/ok</link></item>
    </channel></rss>"""
    items = nr._parse_feed(xml, "S", "ai")
    assert [it["title"] for it in items] == ["Has title"]   # 无标题条目丢弃


def test_parse_feed_no_redline_filter_keeps_prediction_market_terms():
    """绝不沿用 vibe 的 redline 词表:含 polymarket/crypto 的一手资讯照常保留(我们温度计正需它)。"""
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Polymarket odds shift on crypto rate cut</title>
        <link>https://x.com/pm</link><description>bitcoin etc</description></item>
    </channel></rss>"""
    items = nr._parse_feed(xml, "Macro", "macro")
    assert len(items) == 1 and "Polymarket" in items[0]["title"]


def test_load_sources_returns_108_across_12_sectors():
    industries, sources = nr.load_sources()
    assert len(sources) == 108 and len(industries) == 12
    assert all({"name", "hint", "url"} <= set(s) for s in sources)


def test_read_radar_aggregates_filters_by_sector_and_recency(monkeypatch):
    import time as _t
    now = int(_t.time())
    fake = {
        "ai": [{"title": "fresh ai", "url": "u1", "ts": now - 3600, "summary": "", "source": "S1", "sector": "ai"},
               {"title": "stale ai", "url": "u2", "ts": now - 30 * 86400, "summary": "", "source": "S1", "sector": "ai"}],
        "semi": [{"title": "fresh semi", "url": "u3", "ts": now - 7200, "summary": "", "source": "S2", "sector": "semi"}],
    }

    def _fake_fetch(src, opener=None):
        return fake.get(src["hint"], [])
    monkeypatch.setattr(nr, "fetch_feed", _fake_fetch)
    monkeypatch.setattr(nr, "_read_cache", lambda: None)          # 强制现抓
    monkeypatch.setattr(nr, "_write_cache", lambda data: None)
    out = nr.read_radar(sectors=["ai"], days=7, use_cache=False)
    titles = [it["title"] for it in out["items"]]
    assert "fresh ai" in titles                                  # 命中赛道+新鲜
    assert "stale ai" not in titles                              # 超 7 天滤除
    assert "fresh semi" not in titles                            # 非选中赛道不抓


def test_read_radar_swr_warming_when_cold_then_serves_cache(monkeypatch):
    """非阻塞 SWR(web 路由用):无缓存 → 触发后台全量抓 + 返回 warming(不阻塞 108 源);
    有新鲜缓存 → 秒回切片。"""
    import time as _t
    now = int(_t.time())
    monkeypatch.setattr(nr, "_read_cache", lambda: None)
    fired = {"n": 0}
    monkeypatch.setattr(nr, "_trigger_radar", lambda: fired.__setitem__("n", fired["n"] + 1) or True)
    out = nr.read_radar_swr(sectors=None, days=7)
    assert out["warming"] is True and out["items"] == [] and fired["n"] == 1

    cached = {"pulled_ts": now, "pulled_at": "x", "n_sources": 108,
              "items": [{"title": "cached ai", "url": "u", "ts": now - 100, "summary": "",
                         "source": "S", "sector": "ai"}]}
    monkeypatch.setattr(nr, "_read_cache", lambda: cached)
    out2 = nr.read_radar_swr(sectors=["ai"], days=7)
    assert out2["warming"] is False and [it["title"] for it in out2["items"]] == ["cached ai"]
