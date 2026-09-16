# -*- coding: utf-8 -*-
"""datafeed.newsradar 单测(全离线):出版方源解析 + 源表加载 + 聚合(桩 fetch)。
自写 fetcher/parser,不沿用 vibe 的 redline 词表与「先截断后过滤」顺序 bug。"""
import json

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
    assert it["time_basis"] == "published"
    assert it["published_at"].endswith("Z")
    assert it["source_time_raw"] == "Wed, 15 Jul 2026 10:00:00 GMT"


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
    assert items[0]["time_basis"] == "updated"
    assert items[0]["updated_at"] == "2026-07-15T09:00:00Z"


def test_wallstreetcn_official_information_flow_keeps_articles_only():
    payload = {
        "code": 20000,
        "message": "OK",
        "data": {"items": [
            {
                "resource_type": "article",
                "resource": {
                    "title": "Macro article",
                    "uri": "https://wallstreetcn.com/articles/123",
                    "display_time": 1788440066,
                    "content": "<p>article summary</p>",
                },
            },
            {
                "resource_type": "live",
                "resource": {
                    "title": "flash",
                    "uri": "https://wallstreetcn.com/livenews/456",
                    "display_time": 1788440067,
                },
            },
            {
                # The official homepage sometimes injects carousel charts even
                # when ``accept=article``; they are not RSS-equivalent articles.
                "resource_type": "article",
                "resource": {
                    "title": "chart",
                    "uri": "https://wallstreetcn.com/charts/789",
                    "display_time": 1788440068,
                },
            },
        ]},
    }

    items = nr._parse_wallstreetcn_information_flow(
        json.dumps(payload).encode("utf-8"), "华尔街见闻", "macro",
    )

    assert len(items) == 1
    assert items[0]["title"] == "Macro article"
    assert items[0]["summary"] == "article summary"
    assert items[0]["source"] == "华尔街见闻"
    assert items[0]["time_basis"] == "published"
    assert items[0]["published_at"].endswith("Z")


def test_fetch_source_dispatches_versioned_wallstreetcn_json_adapter():
    body = json.dumps({
        "code": 20000,
        "data": {"items": [{
            "resource_type": "article",
            "resource": {
                "title": "Official API article",
                "uri": "https://wallstreetcn.com/articles/123",
                "display_time": 1788440066,
                "content_text": "summary",
            },
        }]},
    }).encode("utf-8")

    class Response:
        status = 200
        headers = {"Content-Type": "application/json; charset=UTF-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return body

    out = nr._fetch_source(
        {
            "name": "华尔街见闻", "hint": "macro",
            "url": "https://api.wscn.net/apiv1/content/information-flow",
            "adapter": "wallstreetcn_information_flow_v1",
        },
        opener=lambda _req, timeout: Response(), max_attempts=1,
    )

    assert out["status"] == "ok" and out["content_type"].startswith("application/json")
    assert out["items_seen"] == 1 and out["items_parsed"] == 1
    assert out["items"][0]["title"] == "Official API article"


def test_parse_feed_generic_date_and_naive_time_are_not_claimed_as_published():
    xml = """<?xml version="1.0"?><rss><channel>
      <item><title>Generic date</title><date>2026-07-15T09:00:00Z</date></item>
      <item><title>Naive date</title><pubDate>2026-07-15T09:00:00</pubDate></item>
    </channel></rss>"""
    generic, naive = nr._parse_feed(xml, "S", "ai")
    assert generic["ts"] > 0 and generic["time_basis"] == "unknown"
    assert generic["published_at"] == "" and generic["source_time_raw"]
    assert naive["ts"] == 0 and naive["time_basis"] == "unknown"
    assert naive["source_time"] == ""
    assert naive["time_status"] == "undated" and naive["is_undated"] is True
    assert naive["undated_reason"] == "source_time_unparseable_or_timezone_missing"
    assert naive["source_time_raw"] == "2026-07-15T09:00:00"


def test_explicit_updated_beats_generic_date_and_date_whitespace_is_normalized():
    xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Both</title><date>2026-08-29  17:47:11   +0800</date>
        <updated>2026-08-30T09:00:00Z</updated></entry>
      <entry><title>Generic only</title><date>2026-08-29  17:47:11   +0800</date></entry>
    </feed>"""
    updated, generic = nr._parse_feed(xml, "S", "tech")
    assert updated["time_basis"] == "updated"
    assert updated["source_time"] == "2026-08-30T09:00:00Z"
    assert generic["time_basis"] == "unknown" and generic["ts"] > 0
    assert nr._decorated_item(updated, "recent")["published"] == ""
    assert nr._decorated_item(generic, "recent")["published"] == ""


def test_fetch_source_exposes_http_200_html_challenge_as_failure():
    class Headers(dict):
        pass

    class Response:
        status = 200
        headers = Headers({"Content-Type": "text/html; charset=utf-8"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"<!doctype html><html><script>challenge()</script></html>"

    out = nr._fetch_source(
        {"name": "challenged", "hint": "tech", "url": "https://example.invalid/feed"},
        opener=lambda _req, timeout: Response(),
    )
    assert out["status"] == "error" and out["http_status"] == 200
    assert out["content_type"].startswith("text/html")
    assert "HTML/challenge" in out["error"]


def test_fetch_source_caps_each_feed_at_vibe_twenty_items():
    class Response:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            body = "".join(
                f"<item><title>item-{i}</title><pubDate>Sat, 29 Aug 2026 {i % 24:02d}:00:00 GMT</pubDate></item>"
                for i in range(25)
            )
            return f"<rss><channel>{body}</channel></rss>".encode()

    out = nr._fetch_source(
        {"name": "large", "hint": "tech", "url": "https://example.invalid/feed"},
        opener=lambda _req, timeout: Response(),
    )
    assert out["status"] == "ok" and len(out["items"]) == 20
    assert out["items_seen"] == 25 and out["items_truncated"] == 5


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


def test_load_sources_returns_current_vibe_catalog_with_versioned_technode_repair():
    industries, sources = nr.load_sources()
    assert len(sources) == 106 and len(industries) == 12
    assert all({"name", "hint", "url"} <= set(s) for s in sources)
    technode = next(source for source in sources if "TechNode" in source["name"])
    assert technode == {
        "name": "TechNode（动点科技英文站）",
        "hint": "tech",
        "type": "rss",
        "url": "https://technode.com/feed/",
        "upstream_url": "https://cn.technode.com/feed/",
        "replacement_version": "technode-official-en@2026-09-03",
        "url_note": (
            "official feed of the same bilingual publisher; the Chinese feed is "
            "live but Cloudflare returns HTTP 403 to the Guanlan runtime egress"
        ),
    }
    wallstreetcn = next(source for source in sources if source["name"] == "华尔街见闻")
    assert wallstreetcn["type"] == "json"
    assert wallstreetcn["adapter"] == "wallstreetcn_information_flow_v1"
    assert wallstreetcn["upstream_url"] == "https://dedicated.wallstreetcn.com/rss.xml"
    assert wallstreetcn["url"].startswith(
        "https://api.wscn.net/apiv1/content/information-flow?")


def test_read_radar_aggregates_filters_by_sector_and_recency(monkeypatch):
    import time as _t
    now = int(_t.time())
    fake = {
        "ai": [{"title": "fresh ai", "url": "u1", "ts": now - 3600, "summary": "", "source": "S1", "sector": "ai"},
               {"title": "stale ai", "url": "u2", "ts": now - 30 * 86400, "summary": "", "source": "S1", "sector": "ai"}],
        "semi": [{"title": "fresh semi", "url": "u3", "ts": now - 7200, "summary": "", "source": "S2", "sector": "semi"}],
    }

    sources = [
        {"name": "S1", "hint": "ai", "url": "https://example.invalid/ai.xml"},
        {"name": "S2", "hint": "semi", "url": "https://example.invalid/semi.xml"},
    ]
    monkeypatch.setattr(nr, "load_sources", lambda: (
        [{"key": "ai", "name": "AI"}, {"key": "semi", "name": "Semi"}], sources,
    ))

    def _fake_fetch(src, opener=None):
        rows = fake.get(src["hint"], [])
        return {
            "source": src["name"], "sector": src["hint"], "url": src["url"],
            "http_status": 200, "status": "ok" if rows else "empty", "error": "",
            "items": rows, "latency_ms": 1, "items_parsed": len(rows),
            "freshest_source_time": "2026-07-15T09:00:00Z",
        }
    monkeypatch.setattr(nr, "_fetch_source", _fake_fetch)
    monkeypatch.setattr(nr, "_read_cache", lambda: None)          # 强制现抓
    monkeypatch.setattr(nr, "_write_cache", lambda data: None)
    out = nr.read_radar(sectors=["ai"], days=7, use_cache=False)
    titles = [it["title"] for it in out["items"]]
    assert "fresh ai" in titles                                  # 命中赛道+新鲜
    assert "stale ai" not in titles                              # 超 7 天滤除
    assert "fresh semi" not in titles                            # 非选中赛道不抓
    assert out["sources_tried"] == out["sources_ok"] == 1
    assert out["source_quality"] == out["quality"] == "complete"


def test_slice_separates_future_and_undated_from_recent():
    import time as _t
    now = int(_t.time())
    data = {
        "cache_schema": 2, "n_sources": 1,
        "source_status": [{"source": "S", "sector": "ai", "status": "ok"}],
        "items": [
            {"title": "recent", "source": "S", "sector": "ai", "ts": now - 10},
            {"title": "future", "source": "S", "sector": "ai", "ts": now + 3600},
            {"title": "undated", "source": "S", "sector": "ai", "ts": 0},
        ],
    }
    default = nr._slice(data, None, 7, 20)
    assert [row["title"] for row in default["items"]] == ["recent"]
    assert default["future_count"] == 1 and default["future_items"] == []
    assert default["undated_count"] == 1
    assert [row["title"] for row in default["undated_items"]] == ["undated"]
    undated = default["undated_items"][0]
    assert undated["time_bucket"] == "undated"
    assert undated["time_status"] == "undated" and undated["is_undated"] is True
    assert undated["undated_reason"] == "source_omitted_time"
    assert undated["time_precision"] == "unknown"
    assert undated["time_confidence"] == 0.0
    explicit = nr._slice(data, None, 7, 20, include_future=True)
    assert [row["title"] for row in explicit["future_items"]] == ["future"]
    assert explicit["future_items"][0]["time_bucket"] == "future_source_time"
    # 旧缓存只有 ts，不能倒推出 published/updated。
    assert explicit["items"][0]["time_basis"] == "unknown"


def test_slice_reports_ok_empty_and_failed_sources_separately():
    data = {
        "cache_schema": 2, "n_sources": 3, "items": [],
        "source_status": [
            {"source": "has", "sector": "ai", "status": "ok"},
            {"source": "empty", "sector": "ai", "status": "empty"},
            {"source": "down", "sector": "semi", "status": "error", "last_error": "timeout"},
        ],
    }
    out = nr._slice(data, None, 7, 20)
    assert (out["sources_tried"], out["sources_ok"], out["sources_with_items"],
            out["sources_empty"], out["sources_failed"]) == (3, 2, 1, 1, 1)
    assert out["source_quality"] == out["quality"] == "degraded"
    assert out["failures"] == [{"source": "down", "sector": "semi", "error": "timeout"}]


def test_failed_refresh_keeps_last_good_items_and_original_data_time(monkeypatch):
    import time as _t
    now = int(_t.time())
    src = {"name": "S", "hint": "ai", "url": "https://example.invalid/feed.xml"}
    cached = {
        "cache_schema": 2, "pulled_ts": now - 100, "pulled_at": "old-data-time",
        "retrieved_at": nr._iso_utc(now - 100), "n_sources": 1,
        "source_status": [{
            "source": "S", "sector": "ai", "url": src["url"], "status": "ok",
            "last_success_at": nr._iso_utc(now - 100), "consecutive_failures": 0,
            "freshest_source_time": nr._iso_utc(now - 160),
        }],
        "items": [{"title": "last good", "source": "S", "sector": "ai",
                   "url": "https://example.invalid/a", "source_url": src["url"],
                   "ts": now - 60}],
    }
    monkeypatch.setattr(nr, "_read_cache", lambda: cached)
    monkeypatch.setattr(nr, "load_sources", lambda: ([{"key": "ai", "name": "AI"}], [src]))
    monkeypatch.setattr(nr, "_fetch_source", lambda _src: {
        "source": "S", "sector": "ai", "url": src["url"], "http_status": None,
        "status": "error", "error": "TimeoutError: timed out", "items": [],
        "latency_ms": 12000, "items_parsed": 0, "freshest_source_time": "",
    })
    written = {}
    monkeypatch.setattr(nr, "_write_cache", lambda data: written.update(data))
    out = nr.read_radar(days=7, use_cache=True, ttl_s=0)
    assert out["pulled_at"] == "old-data-time"
    assert out["items"][0]["title"] == "last good"
    assert out["items"][0]["stale_source"] is True
    assert out["source_status"][0]["served_from_last_good"] is True
    assert out["source_status"][0]["last_success_at"] == nr._iso_utc(now - 100)
    assert out["source_status"][0]["freshest_source_time"] == nr._iso_utc(now - 160)
    assert out["source_quality"] == "degraded" and written["pulled_at"] == "old-data-time"


def test_first_schema_upgrade_all_failed_never_overwrites_legacy_cache_with_empty(monkeypatch):
    import time as _t
    now = int(_t.time())
    src = {"name": "S", "hint": "ai", "url": "https://example.invalid/feed.xml"}
    legacy = {
        "pulled_ts": now - 7200, "pulled_at": "legacy-good", "n_sources": 1,
        "items": [{"title": "legacy survives", "source": "S", "sector": "ai",
                   "url": "https://example.invalid/article", "ts": now - 60}],
    }
    monkeypatch.setattr(nr, "_read_cache", lambda: legacy)
    monkeypatch.setattr(nr, "load_sources", lambda: ([{"key": "ai", "name": "AI"}], [src]))
    monkeypatch.setattr(nr, "_fetch_source", lambda _src: {
        "source": "S", "sector": "ai", "url": src["url"], "status": "error",
        "http_status": None, "content_type": "", "error": "TimeoutError",
        "items": [], "latency_ms": 12000, "items_parsed": 0,
        "freshest_source_time": "",
    })
    written = {}
    monkeypatch.setattr(nr, "_write_cache", lambda data: written.update(data))
    out = nr.read_radar(days=7, use_cache=True, ttl_s=0)
    assert [row["title"] for row in out["items"]] == ["legacy survives"]
    assert written["cache_schema"] == 2 and len(written["items"]) == 1
    assert written["last_good_cache_preserved"] is True


def test_read_radar_swr_warming_when_cold_then_serves_cache(monkeypatch):
    """非阻塞 SWR(web 路由用):无缓存 → 触发后台全量抓 + 返回 warming(不阻塞配置源);
    有新鲜缓存 → 秒回切片。"""
    import time as _t
    now = int(_t.time())
    monkeypatch.setattr(nr, "_read_cache", lambda: None)
    fired = {"n": 0}
    monkeypatch.setattr(nr, "_trigger_radar", lambda: fired.__setitem__("n", fired["n"] + 1) or True)
    out = nr.read_radar_swr(sectors=None, days=7)
    assert out["warming"] is True and out["items"] == [] and fired["n"] == 1

    cached = {"cache_schema": 2, "pulled_ts": now, "pulled_at": "x", "n_sources": 106,
              "items": [{"title": "cached ai", "url": "u", "ts": now - 100, "summary": "",
                         "source": "S", "sector": "ai"}]}
    monkeypatch.setattr(nr, "_read_cache", lambda: cached)
    out2 = nr.read_radar_swr(sectors=["ai"], days=7)
    assert out2["warming"] is False and [it["title"] for it in out2["items"]] == ["cached ai"]


def test_read_radar_swr_cache_only_never_starts_shared_writer(monkeypatch):
    monkeypatch.setattr(nr, "_read_cache", lambda: None)
    monkeypatch.setattr(
        nr, "_trigger_radar",
        lambda: (_ for _ in ()).throw(AssertionError("must not refresh")),
    )

    out = nr.read_radar_swr(days=7, allow_refresh=False)

    assert out["warming"] is False
    assert out["refreshing"] is False
    assert out["cache_only"] is True
    assert "未触发共享后台刷新" in out["note"]


def test_read_radar_swr_serves_legacy_cache_but_triggers_health_upgrade(monkeypatch):
    import time as _t
    now = int(_t.time())
    cached = {
        "pulled_ts": now, "pulled_at": "legacy", "n_sources": 106,
        "items": [{"title": "legacy item", "url": "u", "ts": now - 1,
                   "source": "S", "sector": "ai"}],
    }
    monkeypatch.setattr(nr, "_read_cache", lambda: cached)
    fired = {"n": 0}
    monkeypatch.setattr(nr, "_trigger_radar",
                        lambda: fired.__setitem__("n", fired["n"] + 1) or True)
    out = nr.read_radar_swr(days=7)
    assert [row["title"] for row in out["items"]] == ["legacy item"]
    assert out["source_quality"] == out["quality"] == "unknown"
    assert out["cache_schema_current"] is False and out["refreshing"] is True
    assert fired["n"] == 1


def test_read_radar_swr_backs_off_after_recent_failed_refresh(monkeypatch):
    import time as _t
    now = int(_t.time())
    cached = {
        "cache_schema": 2, "pulled_ts": now - 7200, "pulled_at": "old",
        "refresh_attempted_ts": now - 10, "refresh_attempted_at": "recent-attempt",
        "n_sources": 1, "sources_tried": 1, "sources_ok": 0,
        "source_status": [{"source": "S", "sector": "ai", "status": "error",
                           "last_error": "timeout"}],
        "items": [],
    }
    monkeypatch.setattr(nr, "_read_cache", lambda: cached)
    fired = {"n": 0}
    monkeypatch.setattr(nr, "_trigger_radar",
                        lambda: fired.__setitem__("n", fired["n"] + 1) or True)
    out = nr.read_radar_swr(days=7, ttl_s=60)
    assert out["stale"] is True and out["refreshing"] is False
    assert out["refresh_backoff"] is True and out["refresh_retry_after_s"] > 0
    assert fired["n"] == 0


def test_ingest_slice_is_balanced_per_sector_instead_of_global_top_n(monkeypatch):
    import time as _t
    now = int(_t.time())
    items = [
        {"title": f"macro-{i}", "source": "M", "sector": "macro", "url": f"m{i}",
         "ts": now - i, "source_url": "https://example.invalid/macro.xml"}
        for i in range(250)
    ] + [{
        "title": "ai-only", "source": "A", "sector": "ai", "url": "ai1",
        "ts": now - 400, "source_url": "https://example.invalid/ai.xml",
    }]
    cached = {
        "cache_schema": 2, "pulled_ts": now, "n_sources": 2,
        "industries": [{"key": "macro", "name": "Macro"}, {"key": "ai", "name": "AI"}],
        "source_status": [
            {"source": "M", "sector": "macro", "status": "ok"},
            {"source": "A", "sector": "ai", "status": "ok"},
        ],
        "items": items,
    }
    monkeypatch.setattr(nr, "read_radar", lambda **kwargs: {})
    monkeypatch.setattr(nr, "_read_cache", lambda: cached)
    out = nr.read_radar_for_ingest(days=7, per_sector_limit=200)
    assert len(out["items"]) == 201
    assert out["coverage_by_sector"] == {"macro": 200, "ai": 1}
    assert out["available_by_sector"] == {"macro": 250, "ai": 1}
    assert out["total_available_recent"] == 251 and out["truncated"] is True
    assert any(row["title"] == "ai-only" for row in out["items"])


def test_refresh_is_singleflight_and_cache_write_is_atomic(monkeypatch, tmp_path):
    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    src = {"name": "S", "hint": "ai", "url": "https://example.invalid/feed.xml"}
    monkeypatch.setattr(nr, "_CACHE_PATH", tmp_path / "newsradar.json")
    monkeypatch.setattr(nr, "load_sources", lambda: ([{"key": "ai", "name": "AI"}], [src]))
    calls = {"n": 0}

    def fake(_src):
        calls["n"] += 1
        _t.sleep(0.05)
        now = int(_t.time())
        return {
            "source": "S", "sector": "ai", "url": src["url"], "status": "ok",
            "http_status": 200, "content_type": "application/rss+xml", "error": "",
            "items": [{"title": "one", "source": "S", "sector": "ai", "url": "u",
                       "ts": now}],
            "latency_ms": 1, "items_parsed": 1, "items_seen": 1,
            "items_truncated": 0, "freshest_source_time": nr._iso_utc(now),
        }

    monkeypatch.setattr(nr, "_fetch_source", fake)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _i: nr.read_radar(days=7, ttl_s=60), range(2)))
    assert calls["n"] == 1
    assert all([row["title"] for row in result["items"]] == ["one"] for result in results)
    assert nr._read_cache()["cache_schema"] == 2
    assert list(tmp_path.glob("*.tmp")) == []
