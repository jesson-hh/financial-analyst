# tests/test_seats_decide_exit_action.py
# 持仓语境的**结构化出场评估**(2026-08-02 研究台实证驱动):
#   研究结论(docs/research/2026-08-02-cards-factors-backtest.md):持有偏好是结构问题不是知识问题——
#   为治「几乎不卖」写的两张卖出卡把卖出率从 4.9% 压到 2.1%,顺势票收益 +20.6%→-0.1%。
#   故改结构:持仓语境下 position_action(继续持有/减仓/清仓)成为**必答字段**,
#   模型不能再靠一个被动的「观望」绕过出场评估;它答什么,direction 就由它决定。
# 红线:不带持仓键时,prompt 与落盘逐字节不变(旧形状红线,与 test_seats_decide_hold 同族)。
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
_REPLY = {"v": '{"direction":"观望","confidence":50,"rationale":"r","key_evidence":[]}'}


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


def _rec(tmp_path):
    return _json.loads((tmp_path / "dec.jsonl").read_text(encoding="utf-8").splitlines()[-1])


# ─────────────────────────────────────────────────────────────────────────── #
# 1. prompt 侧:必答字段进 schema + 字段名与 schema 一致(修 side/note 错名)      #
# ─────────────────────────────────────────────────────────────────────────── #
def test_held_schema_demands_position_action(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"观望","confidence":50,"rationale":"r",'
                   '"key_evidence":[],"position_action":"继续持有"}')
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=3)
    assert r["ok"] is True
    u = _CAP["user"]
    assert "position_action" in u                      # 必答字段进了 JSON 格式说明
    assert "继续持有" in u and "减仓" in u and "清仓" in u   # 三个合法取值都点名
    # 错名修复:持仓段不再让模型填 schema 里不存在的 side/note
    assert '"side"' not in u and "side 填" not in u
    assert "note 给理由" not in u


def test_unheld_prompt_has_no_exit_schema(tmp_path, monkeypatch):
    """不带持仓键 → prompt 逐字节回到旧形状(红线)。"""
    _CAP.clear()
    _REPLY["v"] = '{"direction":"观望","confidence":50,"rationale":"r","key_evidence":[]}'
    _post(_client(monkeypatch, tmp_path))
    u = _CAP["user"]
    assert "position_action" not in u and "【持仓】" not in u
    rec = _rec(tmp_path)
    assert "position_action" not in rec and "exit_fraction" not in rec


# ─────────────────────────────────────────────────────────────────────────── #
# 2. 语义侧:position_action 决定 direction(它才是这一问的答案)                 #
# ─────────────────────────────────────────────────────────────────────────── #
def test_clear_out_maps_to_sell(tmp_path, monkeypatch):
    """清仓 → direction=卖出,即使模型在 direction 里惯性填了观望。"""
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"观望","confidence":70,"rationale":"跌破EMA20",'
                   '"key_evidence":[],"position_action":"清仓"}')
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=5)
    assert r["position_action"] == "清仓"
    assert r["exit_fraction"] == 1.0
    assert r["direction"] == "卖出"
    assert _rec(tmp_path)["position_action"] == "清仓"


def test_trim_maps_to_sell_with_half_fraction(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"观望","confidence":60,"rationale":"量价背离",'
                   '"key_evidence":[],"position_action":"减仓"}')
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=5)
    assert r["position_action"] == "减仓"
    assert r["exit_fraction"] == 0.5
    assert r["direction"] == "卖出"


def test_keep_holding_maps_to_watch(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"买入","confidence":80,"rationale":"仍强",'
                   '"key_evidence":[],"position_action":"继续持有"}')
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=5)
    assert r["position_action"] == "继续持有"
    assert r["exit_fraction"] == 0.0
    assert r["direction"] == "观望"       # 已持仓时不重复买入,继续持有 = 观望


def test_missing_position_action_falls_back_to_direction(tmp_path, monkeypatch):
    """模型没给必答字段(老行为/解码残缺)→ 诚实降级为今天的 direction 口径,绝不猜。"""
    _CAP.clear()
    _REPLY["v"] = '{"direction":"卖出","confidence":66,"rationale":"r","key_evidence":[]}'
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=5)
    assert r["direction"] == "卖出"
    assert r["position_action"] is None
    assert r["exit_fraction"] is None


def test_unknown_position_action_is_ignored_not_guessed(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"观望","confidence":50,"rationale":"r",'
                   '"key_evidence":[],"position_action":"加仓"}')
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=5)
    assert r["position_action"] is None       # 非法取值不落地
    assert r["direction"] == "观望"           # 也绝不据此编一个方向


def test_position_action_ignored_when_not_held(tmp_path, monkeypatch):
    """空仓时模型即使乱给 position_action 也不生效(不能凭空卖出没有的仓)。"""
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"买入","confidence":75,"rationale":"r",'
                   '"key_evidence":[],"position_action":"清仓"}')
    r = _post(_client(monkeypatch, tmp_path))
    assert r["direction"] == "买入"
    assert r.get("position_action") is None


# ─────────────────────────────────────────────────────────────────────────── #
# 3. 混合层:结构化出场必须真的进信号(llm_score 跟着走)                          #
# ─────────────────────────────────────────────────────────────────────────── #
def test_exit_action_reaches_llm_score(tmp_path, monkeypatch):
    _CAP.clear()
    _REPLY["v"] = ('{"direction":"观望","confidence":70,"rationale":"r",'
                   '"key_evidence":[],"position_action":"清仓"}')
    r = _post(_client(monkeypatch, tmp_path), hold_entry=10.0, hold_bars=5)
    assert r["llm_score"] == -0.7        # 卖出 → 负分,与 direction 口径一致
