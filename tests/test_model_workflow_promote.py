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
