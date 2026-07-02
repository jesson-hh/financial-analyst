"""factorlib draft 门(P2 §4):status 落盘/list 显形/目录过滤/人审 promote。tmp store 零生产污染。"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.factorlib.api import SaveIn, _promote_factor, _save_factor, build_factorlib_router
from guanlan_v2.factorlib.store import LibraryFactorStore


def _store(tmp_path) -> LibraryFactorStore:
    (tmp_path / "base").mkdir()
    (tmp_path / "mined").mkdir()
    return LibraryFactorStore(base_dir=tmp_path / "base", mined_dir=tmp_path / "mined")


def _client(store) -> TestClient:
    app = FastAPI()
    app.include_router(build_factorlib_router(store=store))
    return TestClient(app)


def test_save_with_draft_status_persisted(tmp_path):
    st = _store(tmp_path)
    r = _save_factor(SaveIn(name="lib_rl_t1", expr="rank(-delta(close,5))", status="draft"), st)
    assert r["ok"] is True
    data = json.loads((st.mined_dir / "lib_rl_t1.json").read_text(encoding="utf-8"))
    assert data[0]["status"] == "draft"
    rows = {f["name"]: f for f in st.list_factors(validate=False)}
    assert rows["lib_rl_t1"]["status"] == "draft"                    # list 显形


def test_save_without_status_unchanged(tmp_path):
    st = _store(tmp_path)
    r = _save_factor(SaveIn(name="lib_rl_t2", expr="rank(-delta(close,5))"), st)
    assert r["ok"] is True
    data = json.loads((st.mined_dir / "lib_rl_t2.json").read_text(encoding="utf-8"))
    assert "status" not in data[0]                                   # 旧行为零变化
    assert "status" not in {f["name"]: f for f in st.list_factors(validate=False)}["lib_rl_t2"]


def test_save_invalid_status_rejected(tmp_path):
    r = _save_factor(SaveIn(name="lib_x", expr="rank(close)", status="published"), _store(tmp_path))
    assert r["ok"] is False and "status" in r["reason"]


def test_promote_strips_status_and_idempotent(tmp_path):
    st = _store(tmp_path)
    _save_factor(SaveIn(name="lib_rl_t3", expr="rank(-delta(close,5))", status="draft"), st)
    r = _promote_factor("lib_rl_t3", st)
    assert r["ok"] is True and r["name"] == "lib_rl_t3"
    data = json.loads((st.mined_dir / "lib_rl_t3.json").read_text(encoding="utf-8"))
    assert "status" not in data[0]
    assert _promote_factor("lib_rl_t3", st)["ok"] is True            # 幂等:已转正再转正仍 ok
    assert _promote_factor("lib_nope", st)["ok"] is False            # 不存在 → not_found
    assert "not_found" in _promote_factor("lib_nope", st)["reason"]


def test_promote_endpoint(tmp_path):
    st = _store(tmp_path)
    _save_factor(SaveIn(name="lib_rl_t4", expr="rank(-delta(close,5))", status="draft"), st)
    c = _client(st)
    j = c.post("/factorlib/promote", json={"name": "lib_rl_t4"}).json()
    assert j["ok"] is True
    j2 = c.post("/factorlib/promote", json={"name": ""}).json()
    assert j2["ok"] is False


def test_catalog_excludes_draft(monkeypatch, tmp_path):
    import guanlan_v2.factorlib.store as fstore
    import guanlan_v2.screen.catalog as cat
    (tmp_path / "base").mkdir()
    (tmp_path / "mined").mkdir()
    monkeypatch.setattr(fstore, "_BASE_DIR", tmp_path / "base")
    monkeypatch.setattr(fstore, "_MINED_DIR", tmp_path / "mined")
    st = LibraryFactorStore()                                        # 读 monkeypatch 后的默认目录
    _save_factor(SaveIn(name="lib_draft_x", expr="rank(-delta(close,5))", status="draft"), st)
    _save_factor(SaveIn(name="lib_ok_y", expr="rank(-delta(close,9))"), st)
    defs = cat._build()
    assert "lib_ok_y" in defs                                        # 正式因子照常上目录
    assert "lib_draft_x" not in defs                                 # draft 不上选股货架(红线)
