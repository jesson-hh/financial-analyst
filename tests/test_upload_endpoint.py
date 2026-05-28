import io
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app

client = TestClient(build_app())


def test_upload_txt_extracts_text():
    r = client.post("/upload", files={"file": ("note.txt", io.BytesIO("贵州茅台 PE 偏高".encode("utf-8")), "text/plain")})
    assert r.status_code == 200
    b = r.json()
    assert b["name"] == "note.txt"
    assert "贵州茅台" in b["text"]
    assert b["chars"] > 0


def test_upload_csv_extracts_text():
    r = client.post("/upload", files={"file": ("h.csv", io.BytesIO(b"code,pct\nSH600519,2.1\n"), "text/csv")})
    assert r.status_code == 200
    assert "SH600519" in r.json()["text"]


def test_upload_rejects_unsupported_type():
    r = client.post("/upload", files={"file": ("a.exe", io.BytesIO(b"MZ..."), "application/octet-stream")})
    assert r.status_code == 400


def test_upload_rejects_oversized():
    big = io.BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    r = client.post("/upload", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413
