import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from guanlan_v2.screen import news as snews
from financial_analyst.data import news_pulse


def test_news_sentiment_delegates_to_news_pulse(monkeypatch):
    monkeypatch.setattr(news_pulse, "fetch_kuaixun",
                        lambda limit=200: [{"time": "2026-06-13 09:31", "title": "降准",
                                            "summary": "", "codes": ["SZ300750"]}])

    async def fake_judge(market, by_code, stock_news, *, llm_json_call):
        return {"ok": True, "as_of": "2026-06-13 09:31", "source": "东方财富 7×24 快讯(实时)",
                "market_read": "偏多", "market_tilt": "利好",
                "sentiment": {"SZ300750": {"tag": "利好", "read": "获批"}},
                "covered": ["SZ300750"],
                "market_evidence": [{"time": "2026-06-13 09:31", "title": "降准"}],
                "evidence_by_code": {"SZ300750": [{"time": "2026-06-13 09:31", "title": "降准"}]},
                "model": "deepseek/deepseek-chat", "note": "ok"}
    monkeypatch.setattr(news_pulse, "judge_sentiment", fake_judge)

    out = asyncio.run(snews.news_sentiment(["SZ300750"]))
    # 既有契约字段须保留(选股页 C 节在用)
    assert out["ok"] is True
    assert out["market_read"] == "偏多"
    assert out["market_tilt"] == "利好"
    assert out["source"].startswith("东方财富")
    assert "SZ300750" in out["sentiment"]
    assert out["covered"] == ["SZ300750"]


def test_news_sentiment_llm_fail_keeps_real_flash(monkeypatch):
    monkeypatch.setattr(news_pulse, "fetch_kuaixun",
                        lambda limit=200: [{"time": "2026-06-13 09:31", "title": "降准",
                                            "summary": "", "codes": ["SZ300750"]}])
    async def judge_fail(market, by_code, stock_news, *, llm_json_call):
        return {"ok": True, "as_of": "2026-06-13 09:31", "source": "东方财富 7×24 快讯(实时)",
                "market_read": None, "market_tilt": None, "sentiment": {}, "covered": ["SZ300750"],
                "market_evidence": [], "evidence_by_code": {}, "model": None,
                "note": "真快讯已取(原文为实);LLM 情绪判读失败:超时"}
    monkeypatch.setattr(news_pulse, "judge_sentiment", judge_fail)
    out = asyncio.run(snews.news_sentiment(["SZ300750"]))
    assert out["ok"] is True                       # 真快讯仍在 → 不整体失败
    assert out["market"] and out["market"][0]["title"] == "降准"   # 原文为实
    assert out["market_read"] is None and out["sentiment"] == {}
    assert "LLM" in out["note"]
