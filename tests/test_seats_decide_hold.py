# tests/test_seats_decide_hold.py
# decide 持仓感知(单元三):payload 可选 hold_entry/hold_bars → 【持仓】块喂 prompt,买后会喊卖;
# 无持仓键/hold_entry<=0 → prompt/落盘逐字节不变(旧记录形状不变红线)。
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


# _DayLoader 固定收盘序列的最后一根(决策 bar)——与 api.py 内 last_close 取值同源同算法。
_LAST_CLOSE = [50 + i * 0.1 + 0.3 for i in range(60)][-1]


def test_hold_injects_block(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=3)
    assert r["ok"] is True
    assert "【持仓】" in _CAP["user"]
    assert "入场价 10.0" in _CAP["user"] and "持有约 3" in _CAP["user"]
    assert "了结卖出" in _CAP["user"]          # 卖出指引真的进了 prompt
    # 浮盈亏 = 桩行情最后收盘/10.0-1(用 _DayLoader 固定收盘价算出精确百分数断言)
    pnl_pct = (_LAST_CLOSE / 10.0 - 1.0) * 100.0
    assert f"{pnl_pct:.2f}%" in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert rec["hold_entry"] == 10.0 and rec["hold_bars"] == 3


def test_no_hold_prompt_unchanged(tmp_path, monkeypatch):
    _CAP.clear()
    _post(_client(monkeypatch, tmp_path))                # 不带持仓键
    assert "【持仓】" not in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert "hold_entry" not in rec and "hold_bars" not in rec   # 旧记录形状不变


def test_hold_entry_nonpositive_ignored(tmp_path, monkeypatch):
    _CAP.clear()
    r = _post(_client(monkeypatch, tmp_path), hold_entry=0)
    assert r["ok"] is True
    assert "【持仓】" not in _CAP["user"]
    rec = _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert "hold_entry" not in rec and "hold_bars" not in rec
