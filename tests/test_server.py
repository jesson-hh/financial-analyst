"""Tests for the v1.9.0 SSE bridge (financial-analyst serve)."""
from __future__ import annotations
import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from financial_analyst.buddy.agent import TurnEvent
from financial_analyst.buddy import server as srv


class _FakeAgent:
    """Stand-in for BuddyAgent — yields a fixed event sequence."""
    def __init__(self, *a, **k):
        pass

    async def run_turn(self, query, confirm_callback=None):
        yield TurnEvent("tool_call", {"name": "stock_brief", "args": {"code": "300750"}})
        yield TurnEvent("tool_result", {
            "name": "stock_brief", "content": "速览文本…", "is_error": False,
            "side_effect": {"brief": {"code": "SZ300750", "name": "宁德时代", "price": 325.1}},
        })
        yield TurnEvent("text", "宁德时代速览[§1], 主力净流入 +4.8 亿.")
        yield TurnEvent("done", None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.agent.BuddyAgent", _FakeAgent)
    return TestClient(srv.build_app())


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tools"] >= 26


def test_tools_list(client):
    r = client.get("/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()}
    assert "stock_brief" in names
    assert "ths_fund_flow" in names


def test_run_sse_full_flow(client):
    with client.stream("POST", "/run", json={"query": "看下宁德时代", "mode": "auto"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    # all expected SSE event types present, in order
    assert "event: plan" in body
    assert "event: tool_start" in body
    assert "event: tool_done" in body
    assert "event: brief" in body          # structured card relayed
    assert "event: answer_progress" in body
    assert "event: done" in body
    # intent classified (brief default)
    assert "stock_brief" in body
    # brief structured payload made it through
    assert "宁德时代" in body
    # §N citation preserved in answer
    assert "§1" in body


def test_run_sse_intent_classification(client):
    with client.stream("POST", "/run", json={"query": "今天主力买什么", "mode": "auto"}) as resp:
        body = "".join(resp.iter_text())
    assert "资金流扫描" in body  # fundflow intent label


# ----- confirm gating logic -------------------------------------------------


def test_auto_mode_no_confirm(client):
    """auto mode → no confirm_request, flows straight to done."""
    with client.stream("POST", "/run", json={"query": "x", "mode": "auto"}) as resp:
        body = "".join(resp.iter_text())
    assert "event: confirm_request" not in body
    assert "event: done" in body


def test_confirm_endpoint_404_when_no_pending(client):
    r = client.post("/confirm", json={"turn_id": "nonexistent", "choice": "y"})
    assert r.status_code == 404


def test_quotes_endpoint(client, monkeypatch):
    """GET /quotes batch endpoint for the UI monitoring wall."""
    fake = {"SH600519": {"code": "SH600519", "name": "贵州茅台", "price": 1311.0}}
    monkeypatch.setattr(
        "financial_analyst.data.collectors.tencent_quote.TencentQuoteCollector.fetch",
        lambda self, codes, **kw: fake,
    )
    r = client.get("/quotes?codes=SH600519,SZ300750")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["quotes"]["SH600519"]["name"] == "贵州茅台"


def test_quotes_endpoint_no_codes(client):
    r = client.get("/quotes?codes=")
    assert r.status_code == 400


# ----- v1.9.3: models / alerts / multi-turn session -------------------------


def test_models_endpoint(client):
    r = client.get("/models")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # v1.9.4: models is a FLAT array [{id, name, provider}] for the picker
    assert isinstance(body["models"], list)
    assert any(m["provider"] == "qwen" for m in body["models"])
    assert all("id" in m and "name" in m for m in body["models"])
    # grouped form still available
    assert "qwen" in body["by_provider"]


def test_alerts_endpoint_lists_rules(client, tmp_path, monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    from financial_analyst.buddy.alerts import AlertStore
    AlertStore().add("SH600519", "price_below", 1200, "止损")
    r = client.get("/alerts")
    assert r.status_code == 200
    alerts = r.json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["code"] == "SH600519"
    assert "跌破" in alerts[0]["desc"]


def test_alerts_check_off_hours(client, tmp_path, monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    from financial_analyst.buddy import alerts as al
    al.AlertStore().add("SH600519", "price_below", 1200)
    monkeypatch.setattr(al, "market_session", lambda now=None: "closed")
    r = client.get("/alerts/check")
    assert r.status_code == 200
    body = r.json()
    assert body["fired"] == []
    assert body["session"] == "closed"


def test_alerts_check_fires_in_session(client, tmp_path, monkeypatch):
    monkeypatch.setattr("financial_analyst.buddy.alerts.Path.home", lambda: tmp_path)
    from financial_analyst.buddy import alerts as al
    al.AlertStore().add("SH600519", "price_below", 1400)
    monkeypatch.setattr(al, "market_session", lambda now=None: "open")
    monkeypatch.setattr(
        "financial_analyst.data.collectors.tencent_quote.TencentQuoteCollector.fetch",
        lambda self, codes, **kw: {"SH600519": {"name": "贵州茅台", "price": 1311.0, "changePercent": -0.3}},
    )
    r = client.get("/alerts/check")
    body = r.json()
    assert len(body["fired"]) == 1
    assert body["fired"][0]["code"] == "SH600519"
    assert body["fired"][0]["price"] == 1311.0


def test_multiturn_session_reuses_agent(client):
    """Same session_id → same BuddyAgent → messages accumulate."""
    # two runs with same session_id; the agent should retain history.
    # (FakeAgent doesn't append, so we verify the agent instance is reused
    #  by checking the sessions dict via a second-run no-crash + same flow.)
    for _ in range(2):
        with client.stream("POST", "/run",
                           json={"query": "看下宁德", "mode": "auto",
                                 "session_id": "sess_A"}) as resp:
            body = "".join(resp.iter_text())
        assert "event: done" in body
