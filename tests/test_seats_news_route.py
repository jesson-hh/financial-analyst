import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from guanlan_v2.seats.api import build_seats_router


def _client():
    app = FastAPI()
    app.include_router(build_seats_router())
    return TestClient(app)


def test_news_route_missing_asof_is_honest_empty():
    r = _client().get("/seats/news?code=SZ000630&mode=pit")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True and j["items"] == [] and j["mode"] == "pit"


def test_news_route_missing_code():
    r = _client().get("/seats/news?mode=pit&asof=2026-06-01")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_news_route_real_pit_smoke():
    import pytest
    if not pathlib.Path(r"G:\stocks\stock_data\pit_store").exists():
        pytest.skip("no pit_store on this machine")
    r = _client().get("/seats/news?code=SZ000630&asof=2026-06-01&mode=pit&window=60")
    j = r.json()
    assert j["ok"] is True and isinstance(j["items"], list)
    assert all(str(it["ts"])[:10] <= "2026-06-01" for it in j["items"])   # 无前视
