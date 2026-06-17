# tests/test_seats_decide_pa.py
# decide 接线价格行为:pa 开→prompt 含两块 + 响应/落盘带 pa_features;pa 关→prompt 不含但响应仍带几何。
import sys
import json as _json
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from guanlan_v2.seats import api as seats_api  # noqa: E402

_CAP = {}


class _CapLLM:
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        _CAP["user"] = messages[-1]["content"]
        return {"choices": [{"message": {
            "content": '{"direction":"观望","confidence":50,"rationale":"r","key_evidence":[]}',
            "reasoning_content": ""}}]}


class _DayLoader:
    def fetch_quote(self, code, start, end, freq):
        ts = pd.date_range("2026-02-01", periods=60, freq="D")
        return pd.DataFrame({"trade_date": ts,
                             "open": [50 + i * 0.1 for i in range(60)],
                             "high": [50 + i * 0.1 + 0.5 for i in range(60)],
                             "low": [50 + i * 0.1 - 0.5 for i in range(60)],
                             "close": [50 + i * 0.1 + 0.3 for i in range(60)],
                             "vol": [1000.0 + i for i in range(60)]})


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _DayLoader())
    monkeypatch.setattr(_llm, "LLMClient", _CapLLM)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def _post(client, **extra):
    body = {"code": "SH600519", "name": "茅台", "date": "2026-04-01",
            "seat_cn": "动量席", "creed": "x", "mode": "fast"}
    body.update(extra)
    return client.post("/seats/decide", json=body).json()


def test_pa_on_injects_blocks_and_returns_features(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path), pa=True, pa_method="我的读法ABC")
    assert r["ok"] is True
    assert isinstance(r.get("pa_features"), dict) and r["pa_features"].get("bar_type")
    assert "【价量形态·确定性" in _CAP["user"]
    assert "我的读法ABC" in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("pa") is True
    assert isinstance(rec.get("pa_features"), dict)


def test_pa_on_empty_method_uses_default(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path), pa=True)
    assert r["ok"] is True
    assert "【价格行为读法" in _CAP["user"]
    assert "T+1" in _CAP["user"]   # 默认模板兜底


def test_pa_off_no_blocks_but_features_present(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path))
    assert r["ok"] is True
    assert isinstance(r.get("pa_features"), dict)          # 几何常显:响应仍带
    assert "【价量形态·确定性" not in _CAP["user"]
    assert "【价格行为读法" not in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec.get("pa") is False
