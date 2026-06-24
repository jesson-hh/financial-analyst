import pandas as pd
import pytest
from guanlan_v2.screen import model_registry as reg


@pytest.mark.slow
def test_train_promote_produces_ranking_and_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    from guanlan_v2.strategy.compute import model_workflow as mw
    spec = {
        "variant_id": "m_wf_test", "name": "工作流lgbm测试", "kind": "lightgbm",
        "recipe": {
            # Ref() is qlib syntax; this engine uses delay() — adapted accordingly
            "features": ["close/delay(close,20)-1", "(close-delay(close,5))/delay(close,5)"],
            "label": "fwd_ret", "fwd_days": 5,
            "universe": "csi300", "start": "2024-01-01", "params": {"leaves": 31, "lr": 0.05},
        },
        "created": "2026-06-22T00:00:00",
    }
    out = mw.train_promote(spec)
    assert out["ok"] is True
    m = reg.variant_meta("m_wf_test")
    assert m["source"] == "workflow" and m["kind"] == "lightgbm"
    assert m["retrainable"] is True and m["recipe"]["features"]
    rank = pd.read_parquet(reg.variant_ranking_path("m_wf_test"))
    assert set(["code", "date", "lgb_pct"]).issubset(rank.columns)
    assert rank["code"].nunique() >= 100
    assert rank["lgb_pct"].between(0, 1).all()


def test_train_promote_rejects_non_tree_kind():
    from guanlan_v2.strategy.compute import model_workflow as mw
    out = mw.train_promote({"variant_id": "x", "kind": "svm", "recipe": {"features": ["a"]}})
    assert out["ok"] is False and "svm" in out["reason"]


def test_train_promote_rejects_empty_features():
    from guanlan_v2.strategy.compute import model_workflow as mw
    out = mw.train_promote({"variant_id": "x", "kind": "lightgbm", "recipe": {"features": []}})
    assert out["ok"] is False


def test_promote_rejects_empty_recipe():
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    c = TestClient(app)
    r = c.post("/model/promote", json={"name": "x", "kind": "lightgbm", "recipe": {"features": []}})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_promote_starts_and_status(monkeypatch):
    import guanlan_v2.workflow.api as wapi
    monkeypatch.setattr(wapi, "_run_promote_subprocess", lambda spec: None)   # 桩掉子进程
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    c = TestClient(app)
    r = c.post("/model/promote",
               json={"name": "x", "kind": "lightgbm",
                     "recipe": {"features": ["delay(close,5)"], "universe": "csi300"}})
    j = r.json()
    assert j["ok"] is True and j["variant_id"].startswith("m_")
    s = c.get("/model/promote/status").json()
    assert s["ok"] is True
    assert s["state"]["variant_id"] == j["variant_id"]   # 状态机真的记录了本次入库
    assert s["state"]["running"] is True                  # stub 不重置 → 仍在跑


def test_screen_models_returns_provenance(tmp_path, monkeypatch):
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    df = pd.DataFrame({"code": [f"SZ{300000+i:06d}" for i in range(120)],
                       "date": "2026-06-19", "lgb_pct": [i/119 for i in range(120)]})
    reg.save_variant("m_wf2", df, {"id": "m_wf2", "name": "wf", "source": "workflow",
                                   "kind": "rf", "recipe": {"features": ["x"]}, "retrainable": True})
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    j = TestClient(app).get("/screen/models").json()
    wf = [v for v in j["variants"] if v["id"] == "m_wf2"][0]
    assert wf["source"] == "workflow" and wf["kind"] == "rf" and wf["retrainable"] is True


def test_workflow_variant_ranking_padded_for_screen(tmp_path, monkeypatch):
    # 工作流模型只产 3 列(code/date/lgb_pct);load_v4_ranking 须补齐 V4_COLUMNS,
    # 使 /screen 的 _screen_via_v4 不再因缺 v4_total/lgb_rank 崩到 toy 回退。
    from guanlan_v2.screen import model_registry as reg
    from guanlan_v2.strategy import ranking as R
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    df = pd.DataFrame({"code": [f"SZ{300000+i:06d}" for i in range(120)],
                       "date": "2026-06-19", "lgb_pct": [i/119 for i in range(120)]})
    reg.save_variant("m_wf3", df, {"id": "m_wf3", "name": "wf", "source": "workflow",
                                   "kind": "lightgbm", "recipe": {"features": ["x"]}, "retrainable": True})
    out = R.load_v4_ranking(model_id="m_wf3")
    for c in R.V4_COLUMNS:
        assert c in out.columns                  # 契约列补齐
    assert out["v4_total"].isna().all()          # 工作流模型无五维 → 全 NaN(→ lgb_pct 分支)
    assert out["lgb_rank"].notna().all() and int(out["lgb_rank"].min()) == 1  # 由 lgb_pct 派生


def test_model_promote_second_concurrent_returns_busy(monkeypatch):
    """Regression: second /model/promote while first is running must return ok:False immediately.
    Pre-fix this deadlocked because _promote_public_state() re-acquired _PROMOTE_LOCK inside
    the guard's `with _PROMOTE_LOCK:` block (non-reentrant lock → permanent self-deadlock)."""
    import guanlan_v2.workflow.api as wapi
    # Reset state so test is idempotent regardless of prior test ordering
    wapi._PROMOTE_STATE.update({"running": False, "phase": "idle", "label": "", "step": 0,
        "started_at": None, "ended_at": None, "ok": None, "error": None,
        "variant_id": None, "lines": []})
    monkeypatch.setattr(wapi, "_run_promote_subprocess", lambda spec: None)  # no-op: leaves running=True
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    c = TestClient(app)
    body = {"name": "concurrent-test", "kind": "lightgbm",
            "recipe": {"features": ["delay(close,5)"], "universe": "csi300"}}
    j1 = c.post("/model/promote", json=body).json()
    assert j1.get("ok") is True, f"first promote should start: {j1}"
    # Second call: would deadlock pre-fix; must return ok:False fast
    j2 = c.post("/model/promote", json=body).json()
    assert j2.get("ok") is False, f"second concurrent promote should be rejected: {j2}"
    assert "reason" in j2, "busy response must include reason"


def test_model_ranking_endpoint(tmp_path, monkeypatch):
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    df = pd.DataFrame({"code": [f"SZ{300000+i:06d}" for i in range(120)],
                       "date": "2026-06-19", "lgb_pct": [i/119 for i in range(120)]})
    reg.save_variant("m_r1", df, {"id": "m_r1", "name": "r"})
    from fastapi.testclient import TestClient
    from guanlan_v2.server import app
    j = TestClient(app).get("/screen/model/ranking?id=m_r1").json()
    assert j["ok"] is True and len(j["rows"]) == 120 and "score" in j["rows"][0]
