import financial_analyst.buddy.server as srv


def _client(monkeypatch, board_fn):
    import financial_analyst.data.etf_board as eb
    monkeypatch.setattr(eb, "etf_market_board", board_fn)
    srv._ETF_BOARD_CACHE["ts"] = 0.0
    srv._ETF_BOARD_CACHE["payload"] = None
    from fastapi.testclient import TestClient
    return TestClient(srv.build_app())


def test_board_ok(monkeypatch):
    calls = {"n": 0}
    def fake_board():
        calls["n"] += 1
        return [{"code": "SH510300", "name": "沪深300ETF", "price": 4.9,
                 "change_pct": -0.1, "amount": 3.2e8, "volume": 6.5e7}]
    c = _client(monkeypatch, fake_board)
    r = c.get("/etf/board")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n"] == 1
    assert body["rows"][0]["code"] == "SH510300"
    c.get("/etf/board")            # within TTL -> cached, helper not called again
    assert calls["n"] == 1


def test_board_error(monkeypatch):
    def boom():
        raise RuntimeError("sina down")
    c = _client(monkeypatch, boom)
    r = c.get("/etf/board")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "sina down" in body["error"]
