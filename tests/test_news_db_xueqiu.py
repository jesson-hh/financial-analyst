import pytest
from pathlib import Path
from financial_analyst.data.news_db import NewsDB


def test_social_posts_upsert_query(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [
        {"id": "1", "ts": "2026-05-18 14:00:00", "author": "user_a",
         "content": "茅台业绩超预期", "likes": 100, "comments_count": 20},
        {"id": "2", "ts": "2026-05-18 15:00:00", "author": "user_b",
         "content": "看空, PE 太高", "likes": 30, "comments_count": 5},
    ]
    n = db.upsert_social_posts("SH600519", items, source="xueqiu_comments")
    assert n == 2
    posts = db.query_social_posts("SH600519", since_days=30)
    assert len(posts) == 2
    db.close()


def test_hot_stocks_upsert_query(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [
        {"rank": 1, "symbol": "600519", "name": "贵州茅台",
         "price": 1323.0, "changePercent": -0.75, "heat": 12345},
        {"rank": 2, "symbol": "300750", "name": "宁德时代",
         "price": 250.0, "changePercent": 2.5, "heat": 9876},
    ]
    db.upsert_hot_stocks(items, source="xueqiu_hot_stock", snapshot_date="2026-05-18")
    rows = db.query_hot_stocks(source="xueqiu_hot_stock")
    assert len(rows) == 2
    assert rows[0]["rank"] == 1
    db.close()


def test_earnings_dates_upsert_query(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [
        {"code": "SH600519", "report_date": "2026-08-15", "quarter": "Q2-2026"},
        {"code": "SH600519", "report_date": "2026-10-30", "quarter": "Q3-2026"},
    ]
    db.upsert_earnings_dates(items, source="xueqiu_earnings")
    upcoming = db.query_earnings_dates(code="SH600519", upcoming_days=365)
    assert len(upcoming) == 2
    assert upcoming[0]["report_date"] == "2026-08-15"  # sorted ASC
    db.close()


def test_stats_includes_new_tables(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    stats = db.stats()
    assert "social_posts" in stats
    assert "hot_stocks" in stats
    assert "earnings_dates" in stats
    db.close()


def test_social_posts_dedupe(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    item = {"id": "1", "ts": "2026-05-18", "content": "x", "likes": 10}
    db.upsert_social_posts("SH600519", [item], source="xueqiu_comments")
    db.upsert_social_posts("SH600519", [item], source="xueqiu_comments")
    assert db.stats()["social_posts"] == 1
    db.close()


def test_social_posts_real_xueqiu_schema(tmp_path):
    """Regression: opencli xueqiu/comments emits items shaped like
    ``{author, text, likes, replies, retweets, created_at, url}`` — no
    explicit ``id`` field. Earlier upsert collapsed every row in a batch
    to the same ``source::code::`` key because the post_id fallback chain
    only knew about ``id`` / ``post_id`` / ``ts``. ``url`` is the only
    stable per-post identifier coming back from xueqiu, so the chain now
    includes it (and ``created_at`` as backup).

    Also confirms ``replies`` lands in the comments_count column."""
    db = NewsDB(path=tmp_path / "test.sqlite")
    real_items = [
        {
            "author": "多伦多的大道信徒",
            "text": "段永平说\"...\"——这句话把择时的逻辑彻底清空了",
            "likes": 8, "replies": 13, "retweets": 2,
            "created_at": "2026-04-28T11:56:41.000Z",
            "url": "https://xueqiu.com/7736566551/386342158",
        },
        {
            "author": "多伦多的大道信徒",
            "text": "段永平说\"任何时代都是难的\"——这三句话是对...",
            "likes": 2, "replies": 2, "retweets": 0,
            "created_at": "2026-04-28T00:27:02.000Z",
            "url": "https://xueqiu.com/7736566551/386156933",
        },
    ]
    n = db.upsert_social_posts("SH600519", real_items, source="xueqiu_comments")
    assert n == 2
    assert db.stats()["social_posts"] == 2, "two distinct urls must produce two rows"

    posts = db.query_social_posts("SH600519", since_days=365)
    assert len(posts) == 2
    # text → content, replies → comments_count
    contents = {p["content"] for p in posts}
    assert any("择时" in c for c in contents)
    by_url = {p["id"]: p for p in posts}
    one = next(p for p in posts if p["likes"] == 8)
    assert one["comments_count"] == 13  # replies mapped through
    assert one["retweet_count"] == 2
    assert one["author"] == "多伦多的大道信徒"
    db.close()
