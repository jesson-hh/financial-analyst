# tests/test_news_sentiment_f10.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier1 import news_sentiment as ns
from financial_analyst.data import f10_corpus as fc


def test_news_sentiment_folds_f10_into_by_code(monkeypatch, tmp_path):
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)
    monkeypatch.setattr(ns.news_pulse, "fetch_kuaixun", lambda limit=200: [])
    monkeypatch.setattr(ns.news_pulse, "fetch_stock_news", lambda code, limit=30: [])

    captured = {}

    async def fake_judge(market, by_code, stock_news, llm_json_call=None):
        captured["by_code"] = by_code
        return {"ok": True, "sentiment": {"SZ000630": {"tag": "中性", "read": "x"}},
                "evidence_by_code": {"SZ000630": by_code.get("SZ000630", [])},
                "covered": ["SZ000630"], "note": ""}
    monkeypatch.setattr(ns.news_pulse, "judge_sentiment", fake_judge)

    agent = ns.NewsSentiment(memory_root=tmp_path)
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    folded = captured["by_code"]["SZ000630"]
    assert any("权益分派" in it["title"] for it in folded)   # F10 事件已折入
    assert out["covered"] is True
    assert out["evidence"] and "权益分派" in out["evidence"][0]["title"]
