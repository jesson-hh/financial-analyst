from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("GUANLAN_FUNDFLOW_DIR", str(tmp_path))
    from guanlan_v2.fundflow.api import build_fundflow_router
    app = FastAPI()
    app.include_router(build_fundflow_router())
    return TestClient(app)


def test_live_endpoint_returns_json(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/fundflow/live?kind=concept&refresh=1")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body


def test_history_endpoint_shape(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.get("/fundflow/history?kind=concept&date=20260708")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"date", "kind", "ticks", "boards", "market_series"}
