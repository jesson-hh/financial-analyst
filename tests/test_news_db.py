import pytest
from pathlib import Path
from financial_analyst.data.news_db import NewsDB


def test_news_db_init_creates_tables(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    stats = db.stats()
    # Core tables always present
    assert stats["news"] == 0
    assert stats["lhb"] == 0
    assert stats["holders"] == 0
    # v1.2 tables
    assert stats["social_posts"] == 0
    assert stats["hot_stocks"] == 0
    assert stats["earnings_dates"] == 0
    db.close()


def test_upsert_and_query_news(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [
        {"time": "2026-05-18 14:00:00", "title": "茅台业绩超预期",
         "summary": "Q1 营收 +12%", "stocks": "1.600519"},
        {"time": "2026-05-18 15:00:00", "title": "央行降准",
         "summary": "释放 5000 亿流动性", "stocks": ""},
    ]
    n = db.upsert_news(items, source="eastmoney_kuaixun")
    assert n == 2
    # Query all recent
    all_news = db.query_news(since_days=30)
    assert len(all_news) == 2
    # Filter by code
    maotai_news = db.query_news(code="SH600519", since_days=30)
    assert len(maotai_news) == 1
    assert "茅台" in maotai_news[0]["title"]


def test_news_fts_search(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    db.upsert_news([
        {"time": "2026-05-18 14:00:00", "title": "茅台 Q1 业绩", "summary": "营收+12%", "stocks": ""},
        {"time": "2026-05-18 15:00:00", "title": "央行降准", "summary": "5000亿", "stocks": ""},
    ], source="test")
    hits = db.search_news("茅台")
    assert len(hits) >= 1
    assert "茅台" in hits[0]["title"]


def test_upsert_and_query_lhb(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [{
        "tradeDate": "2026-05-18", "code": "000021", "name": "深科技",
        "closePrice": 37.03, "changeRate": 10.01,
        "boardAmt": 1e9, "buyAmt": 7.3e8, "sellAmt": 2.7e8, "netAmt": 4.6e8,
        "turnover": 3.4e9, "dealRatio": 29.05, "market": "深交所主板",
        "reason": "日涨幅偏离值达到7%的前5只证券",
    }]
    db.upsert_lhb(items)
    rows = db.query_lhb(code="SZ000021")
    assert len(rows) == 1
    assert rows[0]["change_rate"] == 10.01


def test_upsert_and_query_holders(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [
        {"rank": 1, "reportDate": "2026-03-31",
         "name": "中国贵州茅台酒厂(集团)", "holdNum": 681282935,
         "floatRatio": 54.40, "change": "不变"},
    ]
    db.upsert_holders("SH600519", items)
    rows = db.query_holders("SH600519")
    assert len(rows) == 1
    assert rows[0]["float_ratio"] == 54.40


def test_news_dedup(tmp_path):
    db = NewsDB(path=tmp_path / "test.sqlite")
    items = [{"time": "2026-05-18", "title": "X", "summary": "y"}]
    db.upsert_news(items, source="t")
    db.upsert_news(items, source="t")   # same id → replace, count stays
    assert db.stats()["news"] == 1
