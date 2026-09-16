from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.watch_alerts.api import build_watch_alerts_router
from guanlan_v2.watch_alerts.core import AlertStore, WatchAlertEngine


def test_watch_alerts_crud_api(tmp_path):
    store = AlertStore(tmp_path)
    app = FastAPI()
    app.include_router(build_watch_alerts_router(engine=WatchAlertEngine(store)))
    client = TestClient(app)

    created = client.post("/watch-alerts/rules", json={
        "name": "宁德线", "kind": "stock_price_below", "code": "300750",
        "threshold": 180,
    })
    assert created.status_code == 200
    rule = created.json()["rule"]
    assert rule["enabled"] is False
    assert client.get("/watch-alerts/rules").json()["rules"][0]["id"] == rule["id"]
    assert client.patch(f"/watch-alerts/rules/{rule['id']}", json={"enabled": True}).json()["rule"]["enabled"]
    assert client.delete(f"/watch-alerts/rules/{rule['id']}").status_code == 200
    assert client.get("/watch-alerts/status").json()["rules_total"] == 0


def test_invalid_rule_is_422(tmp_path):
    app = FastAPI()
    app.include_router(build_watch_alerts_router(store=AlertStore(tmp_path)))
    response = TestClient(app).post("/watch-alerts/rules", json={
        "name": "bad", "kind": "made_up", "threshold": 1,
    })
    assert response.status_code == 422
