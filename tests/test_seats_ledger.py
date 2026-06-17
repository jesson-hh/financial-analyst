# -*- coding: utf-8 -*-
# tests/test_seats_ledger.py
# 落子实盘仓位台账(2026-06-12 luozi-run-rework Task 2):
# - POST /seats/ledger:open/trade/decision 三 kind,服务端重放校验(买≤现金、卖≤持仓),
#   非法 → 422 {ok:False,reason};合法 append-only 落盘 var/seats_ledger.jsonl 自动补 ts
# - GET /seats/ledger/state:重放最后一个 open 之后的事件 → 现金/持仓(加权成本)/
#   逐日分组(逆序)/已实现盈亏/胜率/MTM 权益(缺价诚实 equity=null)
# - 再次 open = 重开新账(旧事件留档,state 只从最后一个 open 起算)
# 全部 monkeypatch 到 tmp_path,不碰真 var/;MTM 用 fake loader,不读真数据。
import json
import re
import sys
from pathlib import Path

import pytest

# 优先用在仓 engine/(venv 里的可编辑安装是旧分支)—— 同 tests/test_seats_runs.py 先例。
_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from guanlan_v2.seats import api as seats_api  # noqa: E402


class _FakeLedgerLoader:
    """日线替身:``closes = {数字核: close}``;无键 → 返 None(模拟缺价/坏票)。"""

    def __init__(self, closes):
        self.closes = closes

    def fetch_quote(self, code, start, end, freq):
        import pandas as pd
        px = self.closes.get(re.sub(r"\D", "", str(code)))
        if px is None:
            return None
        return pd.DataFrame({"trade_date": ["2026-06-11"], "close": [float(px)]})


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def _setup(tmp_path, monkeypatch, closes=None) -> TestClient:
    monkeypatch.setattr(seats_api, "_LEDGER_LOG", tmp_path / "seats_ledger.jsonl")
    import financial_analyst.data.loader_factory as _lf
    monkeypatch.setattr(_lf, "get_default_loader",
                        lambda: _FakeLedgerLoader(closes or {}))
    return _client()


def _post(client, ev):
    return client.post("/seats/ledger", json=ev)


def _state(client):
    r = client.get("/seats/ledger/state")
    assert r.status_code == 200
    return r.json()


# ───────────────────────── 1) 开账 + 空账 state ─────────────────────────

def test_ledger_open_and_state(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    # 未开账 → {ok:true, opened:false}
    assert _state(client) == {"ok": True, "opened": False}

    r = _post(client, {"kind": "open", "date": "2026-06-10", "cash": 100000})
    assert r.status_code == 200 and r.json() == {"ok": True}

    s = _state(client)
    assert s["ok"] is True and s["opened"] is True
    assert s["start_date"] == "2026-06-10"
    assert s["init_cash"] == 100000 and s["cash"] == 100000
    assert s["positions"] == [] and s["days"] == []
    assert s["n_positions"] == 0 and s["covered"] == 0
    assert s["equity"] == 100000          # 无持仓 → equity=cash(不取价)
    assert s["realized"] == 0 and s["n_closed"] == 0 and s["win_rate"] is None

    # 非法 open:cash<=0 → 422
    assert _post(client, {"kind": "open", "date": "2026-06-10", "cash": 0}).status_code == 422
    assert _post(client, {"kind": "open", "date": "2026-06-10", "cash": -5}).status_code == 422
    # 未知 kind → 422
    assert _post(client, {"kind": "deposit", "date": "2026-06-10"}).status_code == 422


# ───────────────────────── 2) 交易流:买/卖/超额拒绝/加权成本 ─────────────────────────

def test_ledger_trade_flow(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    # 未开账先 trade → 422
    r0 = _post(client, {"kind": "trade", "date": "2026-06-10", "code": "SH600001",
                        "name": "甲", "side": "buy", "price": 50, "qty": 100})
    assert r0.status_code == 422 and r0.json()["ok"] is False

    _post(client, {"kind": "open", "date": "2026-06-10", "cash": 100000})

    # 买 100 股 @50 → cash 95000、持仓 100@50
    r = _post(client, {"kind": "trade", "date": "2026-06-10", "code": "SH600001",
                       "name": "甲", "side": "buy", "price": 50, "qty": 100})
    assert r.status_code == 200 and r.json() == {"ok": True}
    s = _state(client)
    assert s["cash"] == 95000
    assert s["n_positions"] == 1
    p = s["positions"][0]
    assert p["code"] == "SH600001" and p["qty"] == 100 and p["avg_cost"] == 50

    # 卖 60 股 @55 → cash 98300、剩 40 股(avg_cost 不变)、realized 300、1 笔了结全胜
    r = _post(client, {"kind": "trade", "date": "2026-06-11", "code": "SH600001",
                       "name": "甲", "side": "sell", "price": 55, "qty": 60})
    assert r.status_code == 200
    s = _state(client)
    assert s["cash"] == 98300
    p = s["positions"][0]
    assert p["qty"] == 40 and p["avg_cost"] == 50
    assert s["realized"] == pytest.approx(300)
    assert s["n_closed"] == 1 and s["win_rate"] == 1.0

    # 卖超持仓(100 > 40)→ 422,账不动
    r = _post(client, {"kind": "trade", "date": "2026-06-11", "code": "SH600001",
                       "name": "甲", "side": "sell", "price": 55, "qty": 100})
    assert r.status_code == 422 and r.json()["ok"] is False
    # 买超现金(2000×50=100000 > 98300)→ 422
    r = _post(client, {"kind": "trade", "date": "2026-06-11", "code": "SH600002",
                       "name": "乙", "side": "buy", "price": 50, "qty": 2000})
    assert r.status_code == 422 and r.json()["ok"] is False
    assert _state(client)["cash"] == 98300          # 拒绝的事件绝不落盘

    # 再买同票 100 股 @60 → 加权成本 (40×50+100×60)/140
    r = _post(client, {"kind": "trade", "date": "2026-06-11", "code": "SH600001",
                       "name": "甲", "side": "buy", "price": 60, "qty": 100})
    assert r.status_code == 200
    s = _state(client)
    p = s["positions"][0]
    assert p["qty"] == 140
    assert p["avg_cost"] == pytest.approx((40 * 50 + 100 * 60) / 140)
    assert s["cash"] == pytest.approx(98300 - 6000)

    # 非法 trade 参数 → 422(price<=0 / qty 非正整数 / side 非法)
    base = {"kind": "trade", "date": "2026-06-11", "code": "SH600001",
            "name": "甲", "side": "buy"}
    assert _post(client, {**base, "price": 0, "qty": 1}).status_code == 422
    assert _post(client, {**base, "price": 10, "qty": 0}).status_code == 422
    assert _post(client, {**base, "price": 10, "qty": 1.5}).status_code == 422
    assert _post(client, {**base, "price": 10, "qty": 1, "side": "short"}).status_code == 422


# ───────────────────────── 3) 逐日分组(逆序)+ decision 不动仓位 ─────────────────────────

def test_ledger_days_grouping(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch)
    _post(client, {"kind": "open", "date": "2026-06-09", "cash": 10000})
    _post(client, {"kind": "trade", "date": "2026-06-10", "code": "SH600001", "name": "甲",
                   "side": "buy", "price": 10, "qty": 100, "source": "order"})
    _post(client, {"kind": "decision", "date": "2026-06-10", "code": "SH600001", "name": "甲",
                   "direction": "买入", "confidence": 80, "source": "timer"})
    _post(client, {"kind": "trade", "date": "2026-06-11", "code": "SH600001", "name": "甲",
                   "side": "sell", "price": 11, "qty": 50, "source": "manual"})
    _post(client, {"kind": "decision", "date": "2026-06-11", "code": "SH600001", "name": "甲",
                   "direction": "观望", "confidence": 55, "source": "sentry"})

    s = _state(client)
    # 按日逆序:今日在前
    assert [d["date"] for d in s["days"]] == ["2026-06-11", "2026-06-10"]
    d11, d10 = s["days"]
    assert len(d11["trades"]) == 1 and d11["trades"][0]["side"] == "sell"
    assert len(d11["decisions"]) == 1 and d11["decisions"][0]["direction"] == "观望"
    assert len(d10["trades"]) == 1 and d10["trades"][0]["side"] == "buy"
    assert len(d10["decisions"]) == 1 and d10["decisions"][0]["direction"] == "买入"
    # decision 纯记录不动现金/仓位:10000 − 1000 + 550 = 9550,剩 50 股
    assert s["cash"] == 9550
    assert s["positions"][0]["qty"] == 50


# ───────────────────────── 4) MTM 权益:全覆盖 / 缺价诚实降级 ─────────────────────────

def test_ledger_equity_mtm(tmp_path, monkeypatch):
    client = _setup(tmp_path, monkeypatch,
                    closes={"600001": 55.0, "600002": 22.0})
    _post(client, {"kind": "open", "date": "2026-06-10", "cash": 100000})
    _post(client, {"kind": "trade", "date": "2026-06-10", "code": "SH600001", "name": "甲",
                   "side": "buy", "price": 50, "qty": 100})
    _post(client, {"kind": "trade", "date": "2026-06-10", "code": "SH600002", "name": "乙",
                   "side": "buy", "price": 20, "qty": 50})

    s = _state(client)
    assert s["cash"] == 94000
    assert s["equity"] == pytest.approx(94000 + 100 * 55 + 50 * 22)   # 100600
    assert s["covered"] == 2 and s["n_positions"] == 2
    assert s["equity_date"] == "2026-06-11"
    p1 = next(p for p in s["positions"] if p["code"] == "SH600001")
    assert p1["last_close"] == 55
    assert p1["mkt_value"] == pytest.approx(5500)
    assert p1["upl"] == pytest.approx(500)

    # 某票缺价 → 该持仓 mkt_value=null 且 equity=null,covered 计已估值数
    import financial_analyst.data.loader_factory as _lf
    monkeypatch.setattr(_lf, "get_default_loader",
                        lambda: _FakeLedgerLoader({"600001": 55.0}))
    s2 = _state(client)
    assert s2["equity"] is None and s2["equity_date"] is None
    assert s2["covered"] == 1 and s2["n_positions"] == 2
    p2 = next(p for p in s2["positions"] if p["code"] == "SH600002")
    assert p2["last_close"] is None and p2["mkt_value"] is None and p2["upl"] is None
    p1b = next(p for p in s2["positions"] if p["code"] == "SH600001")
    assert p1b["last_close"] == 55                  # 有价的持仓照常显形
    assert s2["cash"] == 94000                      # 其余字段照常返回


# ───────────────────────── 5) 再次 open = 重开新账(旧事件留档)─────────────────────────

def test_ledger_reopen(tmp_path, monkeypatch):
    log = tmp_path / "seats_ledger.jsonl"
    client = _setup(tmp_path, monkeypatch)
    _post(client, {"kind": "open", "date": "2026-06-01", "cash": 100000})
    _post(client, {"kind": "trade", "date": "2026-06-02", "code": "SH600001", "name": "甲",
                   "side": "buy", "price": 50, "qty": 100})
    r = _post(client, {"kind": "open", "date": "2026-06-10", "cash": 50000})
    assert r.status_code == 200 and r.json() == {"ok": True}

    s = _state(client)
    assert s["opened"] is True and s["start_date"] == "2026-06-10"
    assert s["init_cash"] == 50000 and s["cash"] == 50000
    assert s["positions"] == [] and s["days"] == []
    assert s["realized"] == 0 and s["n_closed"] == 0

    # append-only:旧事件留档绝不改写,且每行自动补 ts
    lines = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    assert [x["kind"] for x in lines] == ["open", "trade", "open"]
    assert lines[0]["cash"] == 100000 and lines[2]["cash"] == 50000
    assert all(x.get("ts") for x in lines)
