import pandas as pd
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


def _client(monkeypatch, tmp_path, with_data=True):
    if with_data:
        pd.DataFrame({"concept_code": ["886001", "886002"],
                      "concept_name": ["CPO", "机器人"]}).to_parquet(tmp_path / "concept_ths_index.parquet")

    class _P:
        parquet_root = tmp_path
    monkeypatch.setattr("financial_analyst.data.paths.get_data_paths", lambda: _P())
    return TestClient(build_app())


def test_concepts_lists_boards(monkeypatch, tmp_path):
    r = _client(monkeypatch, tmp_path).get("/concepts")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    names = [b["name"] for b in body["boards"]]
    assert "CPO" in names and "机器人" in names


def test_concepts_absent_data_is_graceful(monkeypatch, tmp_path):
    r = _client(monkeypatch, tmp_path, with_data=False).get("/concepts")
    assert r.status_code == 200
    assert r.json() == {"available": False, "boards": []}
