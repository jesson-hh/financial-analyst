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


def test_f10_reader_coerces_nondict_llm_fields(monkeypatch, tmp_path):
    """deepseek 偶发把空 dict 字段(lhb_seats/event_classified)返成空 list [];
    旧实现 parsed.get(k, {}) 只在 key 缺失时兜底,key 在但值是 [] 时直灌 F10Output →
    pydantic dict_type 崩 → f10-reader fail → 硬依赖它的 whale 塌 → 整份研报夭折(2026-07-14
    真机 SH603986 实证)。此处断言:非 dict 的 dict 字段被 coerce 回空默认,且能过 F10Output 校验。"""
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)

    async def fake_llm(self, text):
        # 三个字段全给错类型:两个 dict 字段返 list、recent_events 该是 list 却返 dict
        return {"choices": [{"message": {"content": '{"recent_events": {}, "lhb_seats": [], "event_classified": []}'}}]}
    monkeypatch.setattr(fr.F10Reader, "_call_llm", fake_llm)
    agent = fr.F10Reader(memory_root=tmp_path)
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    assert isinstance(out["lhb_seats"], dict) and out["lhb_seats"] == {}
    assert isinstance(out["event_classified"], dict)
    assert set(out["event_classified"]) == {"positive", "negative", "calendar", "neutral"}
    assert isinstance(out["recent_events"], list) and out["recent_events"] == []
    # 复现崩溃路径已封堵:构造 F10Output 不再抛 dict_type
    fr.F10Output(**out)
