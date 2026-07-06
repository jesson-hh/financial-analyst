# -*- coding: utf-8 -*-
"""macro 路由冒烟:透传 pulse/history,不打真 API(monkeypatch 哨兵)。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.macro.api import build_macro_router


def _app():
    app = FastAPI()
    app.include_router(build_macro_router())
    return app


def test_pulse_route_passes_refresh(monkeypatch):
    import guanlan_v2.macro.pulse as mp
    seen = {}

    def fake(refresh=False):
        seen["refresh"] = refresh
        return {"ok": True, "sentinel": "pulse"}

    monkeypatch.setattr(mp, "build_pulse", fake)
    c = TestClient(_app())
    r = c.get("/macro/pulse?refresh=1")
    assert r.status_code == 200 and r.json()["sentinel"] == "pulse"
    assert seen["refresh"] is True
    r2 = c.get("/macro/pulse")
    assert seen["refresh"] is False and r2.status_code == 200


def test_history_route_passes_params(monkeypatch):
    import guanlan_v2.macro.pulse as mp
    seen = {}

    def fake(market_id="", theme=""):
        seen.update(market_id=market_id, theme=theme)
        return [{"ts": "2026-07-06T00:00:00", "prob": 0.5}]

    monkeypatch.setattr(mp, "load_history", fake)
    c = TestClient(_app())
    r = c.get("/macro/history?market_id=pm_1")
    assert r.status_code == 200 and r.json()[0]["prob"] == 0.5
    assert seen == {"market_id": "pm_1", "theme": ""}
