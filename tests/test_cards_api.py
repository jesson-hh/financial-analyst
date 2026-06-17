# guanlan_v2.cards.api · /cards/* 端点测试(FastAPI TestClient,tmp store 注入)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.cards.api import build_cards_router
from guanlan_v2.cards.store import CardStore


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(build_cards_router(CardStore(root=tmp_path)))
    return TestClient(app)


def _payload(**over):
    base = {
        "title": "缩量企稳反转", "cat": "价量", "tags": ["反转", "缩量"],
        "verdict": "通过", "conf": 76, "ic": "0.043",
        "expr": "-rank(ts_sum(ret,5))", "insight": "超跌缩量企稳后反转概率抬升。",
        "src": "研报",
    }
    base.update(over)
    return base


def test_list_empty_initially(tmp_path):
    c = _client(tmp_path)
    r = c.get("/cards/list")
    assert r.status_code == 200
    assert r.json() == {"cards": []}


def test_post_then_list_returns_real_card(tmp_path):
    c = _client(tmp_path)
    r = c.post("/cards", json=_payload())
    assert r.status_code == 200
    cid = r.json()["id"]
    assert cid == "EV-001"
    cards = c.get("/cards/list").json()["cards"]
    assert len(cards) == 1
    card = cards[0]
    assert card["title"] == "缩量企稳反转"
    assert card["conf"] == 76 and isinstance(card["conf"], int)
    assert card["ic"] == "0.043" and isinstance(card["ic"], str)
    assert card["verdict"] == "通过"
    assert card["status"] == "approved"          # 沉淀默认入正式库


def test_post_assigns_sequential_ids(tmp_path):
    c = _client(tmp_path)
    a = c.post("/cards", json=_payload(title="甲")).json()["id"]
    b = c.post("/cards", json=_payload(title="乙")).json()["id"]
    assert a == "EV-001" and b == "EV-002"


def test_get_by_id_and_404(tmp_path):
    c = _client(tmp_path)
    cid = c.post("/cards", json=_payload()).json()["id"]
    assert c.get(f"/cards/{cid}").json()["title"] == "缩量企稳反转"
    assert c.get("/cards/EV-999").status_code == 404


def test_list_status_filter(tmp_path):
    c = _client(tmp_path)
    c.post("/cards", json=_payload(title="草", status="draft"))
    assert c.get("/cards/list?status=approved").json()["cards"] == []
    assert len(c.get("/cards/list?status=draft").json()["cards"]) == 1
    assert len(c.get("/cards/list?status=all").json()["cards"]) == 1


def test_upsert_with_explicit_id_updates(tmp_path):
    c = _client(tmp_path)
    c.post("/cards", json=_payload(id="EV-005", title="原名"))
    c.post("/cards", json=_payload(id="EV-005", title="改名"))
    cards = c.get("/cards/list").json()["cards"]
    assert len(cards) == 1
    assert cards[0]["title"] == "改名"


def test_set_status_endpoint_moves_card(tmp_path):
    c = _client(tmp_path)
    cid = c.post("/cards", json=_payload(status="draft")).json()["id"]
    r = c.post(f"/cards/{cid}/status", json={"status": "approved", "reviewed_by": "xuyi"})
    assert r.status_code == 200
    assert len(c.get("/cards/list?status=approved").json()["cards"]) == 1


def test_set_status_invalid_returns_400(tmp_path):
    c = _client(tmp_path)
    cid = c.post("/cards", json=_payload()).json()["id"]
    assert c.post(f"/cards/{cid}/status", json={"status": "bogus"}).status_code == 400


def test_set_status_missing_card_404(tmp_path):
    c = _client(tmp_path)
    assert c.post("/cards/EV-999/status", json={"status": "approved"}).status_code == 404
