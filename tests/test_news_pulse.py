import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.data import news_pulse as np_


def test_em_to_qlib_parses_market_prefix():
    assert np_.em_to_qlib("1.600030, 0.300750, 90.BK0800") == ["SH600030", "SZ300750"]


def test_em_to_qlib_handles_comma_without_space():
    assert np_.em_to_qlib("1.600030,0.300750") == ["SH600030", "SZ300750"]


def test_fetch_kuaixun_maps_fields(monkeypatch):
    class FakeCollector:
        def fetch(self, limit=50):
            return [{"time": "2026-06-13 09:31:05", "title": "央行降准",
                     "summary": "释放流动性", "stocks": "1.600030, 0.300750"}]
    monkeypatch.setattr(np_, "_kuaixun_collector", lambda: FakeCollector())
    out = np_.fetch_kuaixun(limit=10)
    assert out[0]["time"] == "2026-06-13 09:31"  # 16 字符截断
    assert out[0]["title"] == "央行降准"
    assert out[0]["codes"] == ["SH600030", "SZ300750"]


def test_build_news_prompt_no_stock_news_marks_empty():
    p = np_.build_news_prompt(
        market=[{"time": "2026-06-13 09:31", "title": "央行降准"}],
        by_code={}, stock_news=[])
    assert "央行降准" in p and "无相关" in p


def test_fetch_stock_news_maps_columns(monkeypatch):
    class FakeDF:
        def to_dict(self, orient):
            return [{"新闻标题": "中报预增", "新闻内容": "净利同比+30%",
                     "发布时间": "2026-06-12 20:00:00", "文章来源": "东方财富"}]
    monkeypatch.setattr(np_, "_ak_stock_news", lambda symbol: FakeDF())
    out = np_.fetch_stock_news("SZ300750", limit=5)
    assert out[0]["title"] == "中报预增"
    assert out[0]["source"] == "东方财富"
    assert out[0]["time"] == "2026-06-12 20:00"


def test_fetch_stock_news_degrades_when_akshare_missing(monkeypatch):
    def boom(symbol):
        raise ImportError("no akshare")
    monkeypatch.setattr(np_, "_ak_stock_news", boom)
    out = np_.fetch_stock_news("SZ300750")
    assert out == []   # 降级:不抛,返回空


def _run(coro):
    return asyncio.run(coro)


def test_judge_sentiment_success_filters_uncovered():
    market = [{"time": "2026-06-13 09:31", "title": "央行降准"}]
    by_code = {"SH600030": [{"time": "2026-06-13 09:31", "title": "中信证券获批"}]}

    async def fake_llm(system, user):
        return {"ok": True, "model": "deepseek/deepseek-chat",
                "data": {"market_read": "流动性宽松偏多", "market_tilt": "利好",
                         "by_code": {"SH600030": {"tag": "利好", "read": "获批利好"},
                                     "SZ000001": {"tag": "利好", "read": "编造的"}}}}
    r = _run(np_.judge_sentiment(market, by_code, [], llm_json_call=fake_llm))
    assert r["ok"] is True
    assert r["market_read"] == "流动性宽松偏多" and r["market_tilt"] == "利好"
    assert "SH600030" in r["sentiment"]
    assert "SZ000001" not in r["sentiment"]          # 无快讯的票被过滤,防编造


def test_judge_sentiment_llm_fail_keeps_real_news():
    market = [{"time": "2026-06-13 09:31", "title": "央行降准"}]

    async def fail_llm(system, user):
        return {"ok": False, "reason": "LLM 超时(>45s)"}
    r = _run(np_.judge_sentiment(market, {}, [], llm_json_call=fail_llm))
    assert r["ok"] is True                 # 真快讯仍在 → 整体不失败
    assert r["market_read"] is None and r["sentiment"] == {}
    assert "LLM" in r["note"]
    assert r["market_evidence"][0]["title"] == "央行降准"   # 原文为实


def test_judge_sentiment_no_news_skips_llm_and_is_honest():
    called = {"n": 0}
    async def llm(system, user):
        called["n"] += 1
        return {"ok": True, "data": {}}
    r = _run(np_.judge_sentiment([], {}, None, llm_json_call=llm))
    assert r["ok"] is True and r["market_read"] is None
    assert called["n"] == 0                      # LLM never called when no news
    assert "无相关" in r["note"]
