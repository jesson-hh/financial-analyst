# -*- coding: utf-8 -*-
"""datafeed 路由端点单测(TestClient,桩实现)。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.datafeed.api import build_datafeed_router


def _client():
    app = FastAPI()
    app.include_router(build_datafeed_router())
    return TestClient(app)


def test_market_tape_endpoint(monkeypatch):
    import guanlan_v2.datafeed.market_tape as mt
    monkeypatch.setattr(mt, "read_tape",
                        lambda *a, **k: {"ok": True, "warming": False, "derived": {"zt_count": 5}})
    r = _client().get("/data/market_tape")
    assert r.status_code == 200 and r.json()["derived"]["zt_count"] == 5


def test_data_health_endpoint(monkeypatch):
    monkeypatch.setattr("guanlan_v2.datafeed.health.collect_data_health",
                        lambda: {"ok": True, "overall": {"status": "fresh"}, "items": {}})
    r = _client().get("/data/health")
    assert r.status_code == 200 and r.json()["overall"]["status"] == "fresh"
