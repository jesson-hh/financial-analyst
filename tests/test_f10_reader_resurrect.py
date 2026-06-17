# tests/test_f10_reader_resurrect.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
from financial_analyst.agent.tier1 import f10_reader as fr
from financial_analyst.data import f10_corpus as fc


def test_f10_reader_uses_corpus_without_root(monkeypatch, tmp_path):
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)

    async def fake_llm(self, text):
        assert "权益分派" in text       # LLM 收到确定性 F10 事实
        return {"choices": [{"message": {"content": '{"recent_events": [{"date":"2026-05-29","title":"权益分派"}], "lhb_seats": {}, "event_classified": {"positive":[],"negative":[],"calendar":[],"neutral":[]}}'}}]}
    monkeypatch.setattr(fr.F10Reader, "_call_llm", fake_llm)
    agent = fr.F10Reader(memory_root=tmp_path)   # f10_root 默认 None
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    assert out["recent_events"]
