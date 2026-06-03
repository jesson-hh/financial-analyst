"""/watch/status 返当前 WatchLoop cfg (tick_seconds/cooldown/llm_cap), 让前端能显示."""
import pytest
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


@pytest.fixture
def client():
    app = build_app()
    return TestClient(app)


def test_watch_status_idle_returns_defaults(client):
    """No watch loop running → return WatchLoopConfig defaults (60/15/20)."""
    client.post("/watch/stop", json={})
    r = client.get("/watch/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    # Defaults match WatchLoopConfig (loop.py L107-110)
    assert body["tick_seconds"] == 60
    assert body["cooldown_minutes"] == 15
    assert body["global_llm_cap_per_session"] == 20


def test_watch_status_running_returns_loop_cfg(client):
    """After /watch/start with overrides, /watch/status returns the loop's actual cfg."""
    client.post("/watch/stop", json={})
    r = client.post("/watch/start", json={
        "items": [{"code": "SH600519"}],
        "tick_seconds": 30,
        "cooldown_minutes": 10,
        "global_llm_cap_per_session": 50,
    })
    assert r.status_code == 200
    assert r.json().get("ok") is True
    try:
        r = client.get("/watch/status")
        body = r.json()
        assert body["running"] is True
        assert body["tick_seconds"] == 30
        assert body["cooldown_minutes"] == 10
        assert body["global_llm_cap_per_session"] == 50
    finally:
        client.post("/watch/stop", json={})


def test_watch_status_preserves_existing_fields(client):
    """加 3 字段不破坏现有字段."""
    client.post("/watch/stop", json={})
    r = client.get("/watch/status")
    body = r.json()
    for k in ("ok", "running", "n_items", "items", "tick_count", "llm_calls_made"):
        assert k in body, f"existing field {k} missing"
