from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from financial_analyst.agent.market.market_scanner import MarketScanner
from financial_analyst.agent.market.overseas_market_scanner import OverseasMarketScanner
from financial_analyst.agent.tier1 import news_sentiment as sentiment_module
from financial_analyst.data.pit import PitUnavailableError


def _historical() -> str:
    return (date.today() - timedelta(days=2)).isoformat()


def test_market_proxy_historical_closes_before_pack_and_current_still_reads_it(
    monkeypatch, tmp_path,
):
    calls = []
    agent = MarketScanner(tmp_path)
    monkeypatch.setattr(agent, "_read_evidence_pack", lambda: calls.append("pack") or {
        "sections": {"board_eco": {"as_of": date.today().isoformat(),
                                    "zt_count": 2, "zb_count": 1}},
    })
    with pytest.raises(PitUnavailableError):
        asyncio.run(agent._execute({"asof_date": _historical()}))
    assert calls == []
    output = asyncio.run(agent._execute({"asof_date": date.today().isoformat()}))
    assert calls == ["pack"]
    assert output["n_flagged"] == 3


def test_overseas_proxy_historical_closes_and_current_collector_remains_live(tmp_path):
    class Collector:
        calls = 0

        def fetch_default(self):
            self.calls += 1
            return {"usINX": {"name": "S&P", "price": 6500,
                              "change": 10, "changePercent": 0.2}}

    collector = Collector()
    agent = OverseasMarketScanner(tmp_path, collector=collector)
    with pytest.raises(PitUnavailableError):
        asyncio.run(agent._execute({"asof_date": _historical()}))
    assert collector.calls == 0
    output = asyncio.run(agent._execute({"asof_date": date.today().isoformat()}))
    assert collector.calls == 1 and output["n_indices"] == 1


def test_news_sentiment_historical_skips_live_feeds_current_path_is_preserved(
    monkeypatch, tmp_path,
):
    calls = {"market": 0, "stock": 0, "judge": 0}

    def fetch_market(limit=200):
        calls["market"] += 1
        return [{"time": date.today().isoformat(), "title": "fixture",
                 "codes": ["SH600000"]}]

    def fetch_stock(code, limit=30):
        calls["stock"] += 1
        return []

    async def judge(market, by_code, stock_news, *, llm_json_call):
        calls["judge"] += 1
        return {
            "as_of": date.today().isoformat(), "source": "fixture",
            "market_read": "neutral", "market_tilt": "neutral",
            "sentiment": {"SH600000": {"tag": "neutral", "read": "fixture"}},
            "covered": ["SH600000"],
            "evidence_by_code": {"SH600000": [{"title": "fixture"}]},
            "note": "",
        }

    class Client:
        provider = "fixture"
        model = "fixture"

        @classmethod
        def for_agent(cls, name):
            return cls()

    monkeypatch.setattr(sentiment_module.news_pulse, "fetch_kuaixun", fetch_market)
    monkeypatch.setattr(sentiment_module.news_pulse, "fetch_stock_news", fetch_stock)
    monkeypatch.setattr(sentiment_module.news_pulse, "judge_sentiment", judge)
    monkeypatch.setattr(
        sentiment_module.f10_corpus, "load_facts",
        lambda code, asof: type("Facts", (), {"events": []})(),
    )
    monkeypatch.setattr(sentiment_module, "LLMClient", Client)
    agent = sentiment_module.NewsSentiment(tmp_path)
    historical = asyncio.run(agent._execute({
        "code": "SH600000", "asof_date": _historical(),
    }))
    assert historical["covered"] is False and calls == {"market": 0, "stock": 0, "judge": 0}
    current = asyncio.run(agent._execute({
        "code": "SH600000", "asof_date": date.today().isoformat(),
    }))
    assert current["covered"] is True
    assert calls == {"market": 1, "stock": 1, "judge": 1}
