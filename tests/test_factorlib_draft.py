"""factorlib draft 门(P2 §4):status 落盘/list 显形/目录过滤/人审 promote。tmp store 零生产污染。"""
import json
import multiprocessing

from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.factorlib.api import (
    SaveIn, _promote_factor, _safe_filename, _save_factor, build_factorlib_router,
)
from guanlan_v2.factorlib.store import LibraryFactorStore


def _store(tmp_path) -> LibraryFactorStore:
    (tmp_path / "base").mkdir()
    (tmp_path / "mined").mkdir()
    return LibraryFactorStore(base_dir=tmp_path / "base", mined_dir=tmp_path / "mined")


def _client(store) -> TestClient:
    app = FastAPI()
    app.include_router(build_factorlib_router(store=store))
    return TestClient(app)


def _publish_worker(base_dir, mined_dir, gate, output, name):
    """Spawn-safe helper for the cross-process provenance arbitration test."""
    from guanlan_v2.factorlib.api import SaveIn, save_factor_idempotent
    from guanlan_v2.factorlib.store import LibraryFactorStore

    gate.wait(10)
    result = save_factor_idempotent(
        SaveIn(name=name, expr="rank(close)", source="research_workspace_publish", meta={
            "workspace_id": "rw_aaaaaaaaaaaa",
            "workspace_asset_id": "wa_1111111111111111",
            "workspace_version": 1,
        }),
        LibraryFactorStore(base_dir=base_dir, mined_dir=mined_dir),
        workspace_id="rw_aaaaaaaaaaaa",
        workspace_asset_id="wa_1111111111111111",
        workspace_version=1,
    )
    output.put(result)


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


def test_non_ascii_factor_filename_is_deterministic_for_atomic_arbitration():
    first = _safe_filename("纯中文因子")
    assert first == _safe_filename("纯中文因子")
    assert first != _safe_filename("另一中文因子")
    assert first.startswith("factor_")


def test_same_provenance_is_exactly_once_across_processes(tmp_path):
    base = tmp_path / "base"
    mined = tmp_path / "mined"
    base.mkdir(); mined.mkdir()
    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Event()
    output = ctx.Queue()
    procs = [
        ctx.Process(target=_publish_worker, args=(base, mined, gate, output, name))
        for name in ("first_public_name", "second_public_name")
    ]
    for proc in procs:
        proc.start()
    gate.set()
    results = [output.get(timeout=30) for _ in procs]
    for proc in procs:
        proc.join(timeout=30)
        assert proc.exitcode == 0
    rows = LibraryFactorStore(base_dir=base, mined_dir=mined).load_all()
    assert len(rows) == 1
    assert all(result["ok"] for result in results)
    assert {result["name"] for result in results} == {rows[0]["name"]}


def test_promote_replace_failure_preserves_original_draft(tmp_path, monkeypatch):
    import guanlan_v2.factorlib.api as factor_api

    store = _store(tmp_path)
    saved = _save_factor(SaveIn(
        name="lib_atomic_promote", expr="rank(close)", status="draft",
    ), store)
    target = store.mined_dir / saved["file"]
    before = target.read_bytes()
    real_replace = factor_api.os.replace

    def fail_target_replace(src, dst):
        if str(dst) == str(target):
            raise OSError("simulated promote replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(factor_api.os, "replace", fail_target_replace)
    failed = _promote_factor("lib_atomic_promote", store)
    assert failed["ok"] is False
    assert target.read_bytes() == before
    assert json.loads(before.decode("utf-8"))[0]["status"] == "draft"

    monkeypatch.setattr(factor_api.os, "replace", real_replace)
    assert _promote_factor("lib_atomic_promote", store)["ok"] is True


def test_save_lock_failure_is_honest_and_does_not_write(tmp_path, monkeypatch):
    import contextlib
    import guanlan_v2.factorlib.api as factor_api

    store = _store(tmp_path)

    @contextlib.contextmanager
    def broken_guard(_store):
        raise OSError("lock unavailable")
        yield

    monkeypatch.setattr(factor_api, "_factorlib_write_guard", broken_guard)
    out = _save_factor(SaveIn(name="never_written", expr="rank(close)"), store)
    assert out["ok"] is False and "写锁失败" in out["reason"]
    assert not list(store.mined_dir.glob("*.json"))
    response = _client(store).post("/factorlib/save", json={
        "name": "still_never_written", "expr": "rank(close)",
    })
    assert response.status_code == 200 and response.json()["ok"] is False


def test_public_save_cannot_forge_workspace_publish_provenance(tmp_path):
    store = _store(tmp_path)
    forged = _save_factor(SaveIn(
        name="forged_workspace_publish", expr="rank(close)",
        source="research_workspace_publish",
        meta={
            "workspace_id": "rw_aaaaaaaaaaaa",
            "workspace_asset_id": "wa_1111111111111111",
            "workspace_version": 1,
        },
    ), store)
    assert forged["ok"] is False and "内部发布保留字段" in forged["reason"]
    assert not list(store.mined_dir.glob("*.json"))


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


def test_vintage_sweep_includes_drafts(monkeypatch):
    """P4:vintage 扫描面 = FACTOR_DEFS + factorlib draft(度量不上架)。"""
    import guanlan_v2.screen.factor_vintage as fv

    class _S:
        def list_factors(self, validate=True):
            return [{"name": "lib_rl_x_r0", "expr": "rank(close)", "status": "draft"},
                    {"name": "lib_ok", "expr": "rank(open)"}]          # 非 draft 不并入

    monkeypatch.setattr("guanlan_v2.screen.catalog.FACTOR_DEFS",
                        {"mom20": {"expr": "rank(mom_20)", "family": "动量"}})
    monkeypatch.setattr("guanlan_v2.factorlib.store.LibraryFactorStore", lambda: _S())
    ids = [i[0] for i in fv._sweep_items()]
    assert "mom20" in ids and "lib_rl_x_r0" in ids and "lib_ok" not in ids


def test_factorlib_list_attaches_vintage_for_drafts(monkeypatch, tmp_path):
    st = _store(tmp_path)
    client = _client(st)
    client.post("/factorlib/save", json={"name": "lib_d1", "expr": "rank(close)",
                                         "status": "draft"})
    client.post("/factorlib/save", json={"name": "lib_p1", "expr": "rank(open)"})
    monkeypatch.setattr("guanlan_v2.screen.factor_vintage.cs_vintage_asof",
                        lambda fid, d, **kw: ({"ic": 0.021, "n": 40, "dir": 1,
                                               "asof": "2026-07-02"}
                                              if fid == "lib_d1" else None))
    j = client.get("/factorlib/list?validate=false").json()
    by = {f["name"]: f for f in j["factors"]}
    assert by["lib_d1"]["vintage"] == {"ic": 0.021, "n": 40, "asof": "2026-07-02"}
    assert "vintage" not in by["lib_p1"]        # 只给 draft 附;无值不附(诚实空态)
