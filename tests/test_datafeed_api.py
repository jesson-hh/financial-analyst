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


def test_newsradar_endpoint_forwards_future_and_refresh_flags(monkeypatch):
    import guanlan_v2.datafeed.newsradar as nr
    seen = {}

    def fake(sectors, days, limit, **kwargs):
        seen.update(sectors=sectors, days=days, limit=limit, **kwargs)
        return {"items": [], "future_items": [], "source_quality": "complete"}

    monkeypatch.setattr(nr, "read_radar_swr", fake)
    r = _client().get(
        "/data/newsradar?sectors=ai,semi&days=3&limit=50&include_future=true&refresh=true"
    )
    assert r.status_code == 200
    assert seen == {
        "sectors": ["ai", "semi"], "days": 3, "limit": 50,
        "include_future": True, "force_refresh": True,
    }
    assert _client().get("/data/newsradar?days=31").status_code == 422
    r2 = _client().get("/data/newsradar?sectors=%20AI%20,semi&days=2&limit=3")
    assert r2.status_code == 200 and seen["sectors"] == ["ai", "semi"]
    bad = _client().get("/data/newsradar?sectors=not-a-sector")
    assert bad.status_code == 422 and "未知 NewsRadar 赛道" in bad.json()["detail"]
