"""retrain_variant 重训核心测试。桩掉 train_promote(真训 5min)只验编排:
清死 end / 保留元数据 / 不可重训拒绝 / 变体不存在拒绝 / 端到端极小冒烟(过真 save_variant 复读 asof)。"""
import numpy as np
import pandas as pd


def test_retrain_variant_clears_end_and_preserves_meta(monkeypatch):
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    fake_meta = {
        "id": "m_x", "name": "老变体", "kind": "lightgbm", "source": "workflow",
        "retrainable": True, "created": "2026-04-01T00:00:00", "status": "draft",
        "recipe": {"features": ["delay(close,5)"], "universe": "fullA_active_20260401",
                   "codes": ["SH600000"], "start": "2022-01-01", "end": "2026-04-01"},
        "asof": "2026-07-10", "oos_ic": 0.03,
    }
    monkeypatch.setattr(reg, "variant_meta", lambda v: {k: (dict(x) if isinstance(x, dict) else x)
                                                        for k, x in fake_meta.items()})
    captured = {}

    def fake_promote(spec):
        captured["spec"] = spec
        return {"ok": True, "variant_id": spec["variant_id"], "oos_ic": 0.05}

    monkeypatch.setattr(mw, "train_promote", fake_promote)

    out = mw.retrain_variant("m_x")
    spec = captured["spec"]
    assert "end" not in spec["recipe"]                       # 死 end 已清(核心修法)
    assert spec["recipe"]["universe"] == "fullA_active_20260401"  # 股池快照原样(不滚)
    assert spec["recipe"]["codes"] == ["SH600000"]           # 固定股列表随之保留
    assert spec["name"] == "老变体" and spec["kind"] == "lightgbm"
    assert spec["created"] == "2026-04-01T00:00:00"          # created 保留
    assert spec["status"] == "draft"                          # draft 语义保留
    assert out["ok"] is True and out["variant_id"] == "m_x"
    assert out["date"] == "2026-07-10"                        # 复读 asof
    assert out["universe"] == "fullA_active_20260401"
    assert "快照" in out["universe_note"]                     # 诚实标注股池未滚


def test_retrain_variant_does_not_mutate_original_recipe(monkeypatch):
    """pop('end') 只作用于 spec 的副本,绝不改动 meta 原 recipe。"""
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    original_recipe = {"features": ["f1"], "universe": "csi300", "end": "2026-04-01"}
    meta = {"id": "m_y", "name": "n", "kind": "rf", "retrainable": True,
            "created": "", "recipe": original_recipe}
    monkeypatch.setattr(reg, "variant_meta", lambda v: meta)
    monkeypatch.setattr(mw, "train_promote",
                        lambda spec: {"ok": True, "variant_id": spec["variant_id"]})
    mw.retrain_variant("m_y")
    assert original_recipe["end"] == "2026-04-01"             # 原 recipe 的 end 未被 pop


def test_retrain_variant_rejects_non_retrainable(monkeypatch):
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    # v4-lgb workshop 类:retrainable=False → 诚实拒绝,绝不假装
    monkeypatch.setattr(reg, "variant_meta", lambda v: {
        "id": v, "name": "prod-like", "kind": "v4-lgb", "source": "workshop",
        "retrainable": False, "recipe": {}})
    out = mw.retrain_variant("prod")
    assert out["ok"] is False and "retrainable=False" in out["reason"]


def test_retrain_variant_rejects_unsupported_kind(monkeypatch):
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    # retrainable=True 但 kind 不在树模型集 → 仍拒绝(诚实)
    monkeypatch.setattr(reg, "variant_meta", lambda v: {
        "id": v, "name": "x", "kind": "svm", "retrainable": True, "recipe": {"features": ["a"]}})
    out = mw.retrain_variant("m_svm")
    assert out["ok"] is False and "kind=svm" in out["reason"]


def test_retrain_variant_missing(monkeypatch):
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "variant_meta", lambda v: {})   # 缺失 → 空 dict
    out = mw.retrain_variant("nope")
    assert out["ok"] is False and out["reason"] == "变体不存在"


def test_retrain_variant_passes_through_failure(monkeypatch):
    """train_promote 失败 → 原样透传 reason,不再复读 asof / 不补 universe。"""
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "variant_meta", lambda v: {
        "id": v, "name": "n", "kind": "lightgbm", "retrainable": True,
        "recipe": {"features": ["f1"], "universe": "csi300"}})
    monkeypatch.setattr(mw, "train_promote",
                        lambda spec: {"ok": False, "reason": "训练样本太少(3)"})
    out = mw.retrain_variant("m_fail")
    assert out["ok"] is False and out["reason"] == "训练样本太少(3)"
    assert "universe_note" not in out                        # 失败不补收尾字段


def test_retrain_variant_smoke_end_to_end(tmp_path, monkeypatch):
    """极小冒烟:桩掉 materialize/build_model/latest_trade_date(避免真训/真 IO),
    但过真 save_variant → 复读 asof/元数据保留/死 end 已清 均在真 meta.json 上验证。"""
    import guanlan_v2.workflow.api as wapi
    from guanlan_v2.strategy.compute import model_workflow as mw
    from guanlan_v2.screen import model_registry as reg
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    # 预置一个带死 end 的 lightgbm 变体
    df0 = pd.DataFrame({"code": [f"SH60{i:04d}" for i in range(120)],
                        "date": "2026-04-01", "lgb_pct": [i / 119 for i in range(120)]})
    reg.save_variant("m_rt", df0, {
        "id": "m_rt", "name": "重训冒烟", "source": "workflow", "kind": "lightgbm",
        "retrainable": True, "created": "2026-04-01T00:00:00",
        "recipe": {"features": ["f1"], "universe": "csi_fast", "start": "2026-01-01",
                   "end": "2026-04-01"}, "asof": "2026-04-01", "oos_ic": 0.01})

    captured = {}

    def fake_mat(body, universe, feats, start, end):
        captured["end"] = end                                # 应为清 end 后取的最新交易日
        idx = pd.MultiIndex.from_product(
            [pd.date_range("2026-05-01", periods=30), [f"SH60{i:04d}" for i in range(20)]],
            names=["datetime", "code"])
        fe = pd.DataFrame({"f1": range(len(idx))}, index=idx, dtype="float64")
        lab = pd.Series(range(len(idx)), index=idx, dtype="float64")
        return (None, fe, lab, ["f1"])

    class _M:
        def fit(self, X, y):
            return self

        def predict(self, X):
            return np.arange(len(X), dtype="float64")

    monkeypatch.setattr(wapi, "_materialize_xy", fake_mat)
    monkeypatch.setattr(wapi, "_build_model", lambda k, p: (_M(), {"ok": True}))
    monkeypatch.setattr(mw, "_holdout_oos_ic", lambda *a, **kw: 0.02)
    monkeypatch.setattr("guanlan_v2.strategy.compute.regen._latest_trade_date",
                        lambda p: "2026-07-10")

    out = mw.retrain_variant("m_rt")
    assert out["ok"] is True and out["variant_id"] == "m_rt"
    assert captured["end"] == "2026-07-10"                   # 死 end 清后取到新最新交易日
    m2 = reg.variant_meta("m_rt")
    assert m2["name"] == "重训冒烟" and m2["created"] == "2026-04-01T00:00:00"  # 元数据保留
    assert m2["kind"] == "lightgbm" and m2["retrainable"] is True
    assert "end" not in m2["recipe"]                          # 死 end 已清(落库后)
    assert m2["recipe"]["universe"] == "csi_fast"            # 股池快照未滚
    assert m2["asof"] == "2026-05-30" and out["date"] == m2["asof"]  # asof 滚到面板最新日
    assert m2["asof"] != "2026-04-01"                        # 不再冻在旧日期
