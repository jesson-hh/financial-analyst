# tests/test_seats_decide_intraday.py
# 后端 decide 加 freq=30min(luozi intraday Task 1):
# - _agg_5min_to_30min:5min → 30min 聚合(perGroup=6,按交易日分组不跨日)
# - POST /seats/decide freq=30min 分支:PIT ≤决策时刻、asof 带时分、因子跑在 30 分钟序列上
# - freq=day 既有行为完全不变(asof 仅日期、落盘无 freq 不影响日线断言)
# 全部 monkeypatch 到 tmp_path / Fake LLM / Fake loader,不碰真 var/ 与真 LLM。
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd  # noqa: E402
from guanlan_v2.seats import api as seats_api  # noqa: E402


def _mk5(day, n, base=50.0):
    ts = pd.date_range(f"{day} 09:35", periods=n, freq="5min")
    return pd.DataFrame({
        "trade_date": ts,
        "open": [base + i * 0.1 for i in range(n)],
        "high": [base + i * 0.1 + 0.05 for i in range(n)],
        "low": [base + i * 0.1 - 0.05 for i in range(n)],
        "close": [base + i * 0.1 for i in range(n)],
        "vol": [1000.0] * n,
        "amount": [base * 1000.0 * (1 + i * 0.002) for i in range(n)],
    })


def test_agg_5min_to_30min_perGroup6():
    df5 = _mk5("2026-06-11", 12)
    df30 = seats_api._agg_5min_to_30min(df5)
    assert len(df30) == 2
    assert abs(float(df30["close"].iloc[0]) - float(df5["close"].iloc[5])) < 1e-9
    assert abs(float(df30["close"].iloc[1]) - float(df5["close"].iloc[11])) < 1e-9
    assert float(df30["vol"].iloc[0]) == 6000.0
    assert abs(float(df30["high"].iloc[0]) - float(df5["high"].iloc[0:6].max())) < 1e-9
    assert abs(float(df30["low"].iloc[0]) - float(df5["low"].iloc[0:6].min())) < 1e-9
    assert str(df30["trade_date"].iloc[0]) == str(df5["trade_date"].iloc[5])


def test_agg_groups_by_day_no_cross_lunch():
    df5 = pd.concat([_mk5("2026-06-10", 6), _mk5("2026-06-11", 6)], ignore_index=True)
    df30 = seats_api._agg_5min_to_30min(df5)
    assert len(df30) == 2
    assert str(df30["trade_date"].iloc[0])[:10] == "2026-06-10"
    assert str(df30["trade_date"].iloc[1])[:10] == "2026-06-11"


def test_agg_empty_and_short():
    assert len(seats_api._agg_5min_to_30min(pd.DataFrame())) == 0
    df30 = seats_api._agg_5min_to_30min(_mk5("2026-06-11", 4))
    assert len(df30) == 1


from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeLLM:
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        return {"choices": [{"message": {
            "content": '{"direction":"观望","confidence":55,"rationale":"分钟桩","key_evidence":["e"]}',
            "reasoning_content": ""}}]}


class _FakeLoader5:
    def fetch_quote(self, code, start, end, freq):
        if freq == "5min":
            return pd.concat([_mk5("2026-06-10", 12), _mk5("2026-06-11", 12, base=52.0)], ignore_index=True)
        return pd.DataFrame()


def _client_intraday(monkeypatch, tmp_path):
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader5())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLM)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def test_decide_freq30min_pit_and_factors(tmp_path, monkeypatch):
    client = _client_intraday(monkeypatch, tmp_path)
    r = client.post("/seats/decide", json={
        "code": "SH605358", "name": "立昂微", "date": "2026-06-11 10:05",
        "seat_cn": "动量席", "creed": "测试", "mode": "fast", "freq": "30min"})
    j = r.json()
    assert j["ok"] is True
    assert ":" in str(j["asof"])
    assert str(j["asof"]) <= "2026-06-11 10:05"
    assert isinstance(j.get("factors"), dict) and len(j["factors"]) > 0
    import json as _json
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("freq") == "30min"
    assert ":" in str(rec.get("asof"))


def test_decide_freq_day_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm

    class _DayLoader:
        def fetch_quote(self, code, start, end, freq):
            ts = pd.date_range("2026-05-01", periods=80, freq="D")
            return pd.DataFrame({"trade_date": ts, "open": 50.0, "high": 51.0, "low": 49.0,
                                 "close": [50 + i * 0.1 for i in range(80)], "vol": 1000.0})
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _DayLoader())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLM)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    c = TestClient(app)
    j = c.post("/seats/decide", json={"code": "SH600519", "name": "茅台", "date": "2026-06-11",
                                      "seat_cn": "动量席", "creed": "x", "mode": "fast"}).json()
    assert j["ok"] is True and ":" not in str(j["asof"])
