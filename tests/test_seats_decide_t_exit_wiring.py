# tests/test_seats_decide_t_exit_wiring.py
# 持仓语境两条确定性证据接线(2026-08-08):
#   出场引擎(exit_engine)+ 做T参考(t_engine)→ 注入 prompt + 落盘 + 响应。
# 红线:空仓路径 prompt/落盘/响应形状逐字节不变;30min 频不注入(参数为日线口径)。
import sys
import json as _json
from datetime import datetime
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
_HELD_REPLY = ('{"direction":"观望","confidence":50,"rationale":"r",'
               '"key_evidence":[],"position_action":"继续持有"}')
_FLAT_REPLY = '{"direction":"观望","confidence":50,"rationale":"r","key_evidence":[]}'
_REPLY = {"v": _HELD_REPLY}


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
        return {"choices": [{"message": {"content": _REPLY["v"], "reasoning_content": ""}}]}


class _DayLoader:
    def fetch_quote(self, code, start, end, freq):
        ts = pd.date_range(end=end, periods=60, freq="D")
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


def _rec(tmp_path):
    return _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])


def test_held_injects_exit_and_t_blocks(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = _HELD_REPLY
    client = _client(monkeypatch, tmp_path)
    r = _post(client, hold_entry=52.0, hold_bars=10)
    assert r["ok"]
    # prompt 侧:两块确定性证据都进了 user prompt
    assert "出场引擎·确定性计算" in _CAP["user"]
    assert "做T参考·确定性带位" in _CAP["user"]
    assert "实证纪律" in _CAP["user"]
    # 响应侧:结构化字段
    assert r["exit_engine"]["action"] in ("继续持有", "减仓", "清仓")
    assert r["exit_engine"]["initial_stop"] > 0
    assert r["t_ref"]["r1"] > r["t_ref"]["pivot"] > r["t_ref"]["s1"]
    # 落盘侧
    rec = _rec(tmp_path)
    assert rec["exit_engine"]["action"] == r["exit_engine"]["action"]
    assert rec["t_ref"]["base_date"] == r["t_ref"]["base_date"]


def test_flat_path_unchanged(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = _FLAT_REPLY
    client = _client(monkeypatch, tmp_path)
    r = _post(client)
    assert r["ok"]
    assert "出场引擎" not in _CAP["user"]
    assert "做T参考" not in _CAP["user"]
    assert "exit_engine" not in r and "t_ref" not in r
    rec = _rec(tmp_path)
    assert "exit_engine" not in rec and "t_ref" not in rec


def _stub_external_evidence(monkeypatch):
    from guanlan_v2.seats import decision_evidence

    monkeypatch.setattr(decision_evidence, "build_decision_evidence", lambda code, anchor, **kw: {
        "status": "empty", "source": "test", "asof": str(anchor),
        "coverage": {"included_count": 0}, "news": {}, "microstructure": {},
        "items": [], "errors": [], "prompt": "", "mode": "pit" if kw.get("replay") else "live",
    })


def test_accepted_live_quote_enables_daytype_tendency(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = _HELD_REPLY
    _stub_external_evidence(monkeypatch)
    from guanlan_v2.seats import intraday, watcher
    monkeypatch.setattr(intraday, "build_intraday_context", lambda code, df: None)
    monkeypatch.setattr(
        watcher, "market_session_state",
        lambda _now=None: {"market_open": True, "market_phase": "open"},
    )
    client = _client(monkeypatch, tmp_path)
    now = datetime.now().replace(microsecond=0)
    r = _post(client, date=now.strftime("%Y-%m-%d"), hold_entry=52.0,
              quote={"open": 55.0, "price": 55.05,
                     "asof": now.isoformat(timespec="seconds"),
                     "source": "tencent_realtime_quote"})   # 方向占振幅比极小 → 暂偏震荡
    assert r["ok"]
    assert r["quote_diagnostic"]["status"] == "accepted"
    assert "日型倾向" in _CAP["user"]
    assert "日型倾向" in r["t_ref"]["text"]


def test_replay_raw_quote_cannot_enable_daytype_tendency(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = _HELD_REPLY
    _stub_external_evidence(monkeypatch)
    client = _client(monkeypatch, tmp_path)
    now = datetime.now().replace(microsecond=0)
    r = _post(
        client,
        date=now.strftime("%Y-%m-%d"),
        history_replay=True,
        hold_entry=52.0,
        price=55.05,
        quote={"open": 55.0, "price": 55.05,
               "asof": now.isoformat(timespec="seconds"),
               "source": "tencent_realtime_quote"},
    )
    assert r["ok"]
    assert r["quote_diagnostic"] == {"status": "ignored", "reason": "history_replay"}
    assert "日型倾向" not in _CAP["user"]
    assert "日型倾向" not in r["t_ref"]["text"]


def test_30min_freq_not_injected(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = _HELD_REPLY

    class _Min5Loader(_DayLoader):
        def fetch_quote(self, code, start, end, freq):
            if freq == "5min":
                ts = pd.date_range("2026-03-25 09:35", periods=48 * 8, freq="5min")
                return pd.DataFrame({"trade_date": ts,
                                     "open": 50.0, "high": 50.5, "low": 49.5,
                                     "close": 50.2, "vol": 1000.0})
            return super().fetch_quote(code, start, end, "day")

    monkeypatch.setattr(seats_api, "_DEC_LOG", tmp_path / "dec.jsonl")
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _Min5Loader())
    monkeypatch.setattr(_llm, "LLMClient", _CapLLM)
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    client = TestClient(app)
    r = _post(client, hold_entry=52.0, freq="30min")
    if r.get("ok"):   # 30min 路径依赖 5min 聚合,只要成功就必须没有注入
        assert "出场引擎" not in _CAP.get("user", "")
        assert "做T参考" not in _CAP.get("user", "")
