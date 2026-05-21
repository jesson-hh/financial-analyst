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
