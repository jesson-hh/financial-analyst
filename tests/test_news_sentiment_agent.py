import sys
import pathlib
import asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.agent.tier1.news_sentiment import NewsSentiment, NewsSentimentOutput
from financial_analyst.data import news_pulse


def test_news_sentiment_maps_single_code(tmp_path, monkeypatch):
    monkeypatch.setattr(news_pulse, "fetch_kuaixun",
                        lambda limit=200: [{"time": "2026-06-13 09:31", "title": "获批",
                                            "summary": "", "codes": ["SZ300750"]}])
    monkeypatch.setattr(news_pulse, "fetch_stock_news", lambda code, **_: [])

    async def fake_judge(market, by_code, stock_news, *, llm_json_call):
        return {"ok": True, "as_of": "2026-06-13 09:31", "source": "东方财富 7×24 快讯(实时)",
                "market_read": "偏多", "market_tilt": "利好",
                "sentiment": {"SZ300750": {"tag": "利好", "read": "获批利好"}},
                "covered": ["SZ300750"], "market_evidence": [{"time": "2026-06-13 09:31", "title": "获批"}],
                "evidence_by_code": {"SZ300750": [{"time": "2026-06-13 09:31", "title": "获批"}]},
                "model": "deepseek/deepseek-chat", "note": "ok"}
    monkeypatch.setattr(news_pulse, "judge_sentiment", fake_judge)

    agent = NewsSentiment(memory_root=tmp_path)
    res = asyncio.run(agent.run({"code": "SZ300750", "asof_date": "2026-06-13"}))
    assert res.ok is True
    out: NewsSentimentOutput = res.output
    assert out.code == "SZ300750" and out.market_read == "偏多"
    assert out.stock_tilt == "利好" and out.covered is True
    assert out.evidence and out.evidence[0]["title"] == "获批"


def test_news_sentiment_honest_when_fetch_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(news_pulse, "fetch_kuaixun", lambda limit=200: [])
    monkeypatch.setattr(news_pulse, "fetch_stock_news", lambda code, **_: [])

    async def judge_empty(market, by_code, stock_news, *, llm_json_call):
        return {"ok": True, "as_of": None, "source": "东方财富 7×24 快讯(实时)",
                "market_read": None, "market_tilt": None, "sentiment": {}, "covered": [],
                "market_evidence": [], "evidence_by_code": {}, "model": None,
                "note": "近期无相关快讯;不编造"}
    monkeypatch.setattr(news_pulse, "judge_sentiment", judge_empty)

    agent = NewsSentiment(memory_root=tmp_path)
    res = asyncio.run(agent.run({"code": "SZ300750", "asof_date": "2026-06-13"}))
    assert res.ok is True and res.output.covered is False
    assert "无相关" in res.output.honest_note


def test_news_sentiment_honest_when_fetch_raises(tmp_path, monkeypatch):
    def boom(limit=200):
        raise RuntimeError("timeout")
    monkeypatch.setattr(news_pulse, "fetch_kuaixun", boom)
    agent = NewsSentiment(memory_root=tmp_path)
    res = asyncio.run(agent.run({"code": "SZ300750", "asof_date": "2026-06-13"}))
    assert res.ok is True and res.output.covered is False
    assert "快讯拉取失败" in res.output.honest_note


import yaml


def test_swarm_yaml_wires_news_sentiment_to_writer():
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ["config/swarm/stock-deep-dive.yaml",
                "engine/financial_analyst/_resources/config/swarm/stock-deep-dive.yaml"]:
        cfg = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
        names = [a["name"] for a in cfg["agents"]]
        assert "news-sentiment" in names, f"{rel} 缺 news-sentiment 节点"
        rw = next(a for a in cfg["agents"] if a["name"] == "report-writer")
        assert "news-sentiment" in rw["deps"], f"{rel} report-writer.deps 缺"
        assert "news-sentiment" in rw["input_keys"], f"{rel} report-writer.input_keys 缺"
        assert set(rw["deps"]) <= set(rw["input_keys"]), \
            f"{rel}: deps not all in input_keys: {set(rw['deps']) - set(rw['input_keys'])}"


def test_news_sentiment_registered_in_tui():
    from financial_analyst import tui
    from financial_analyst.agent.registry import SubAgentRegistry
    from financial_analyst.agent.tier1.news_sentiment import NewsSentiment
    # Real function name is _ensure_registered (not _register_agents)
    tui._ensure_registered()
    assert "news-sentiment" in SubAgentRegistry.names()
    # Prove the name maps to the RIGHT class (guards against shared-registry
    # false-pass). Registry exposes no get() getter — lookup is the raw
    # _registry dict (or build()/names()); assert class identity directly.
    assert SubAgentRegistry._registry["news-sentiment"] is NewsSentiment
