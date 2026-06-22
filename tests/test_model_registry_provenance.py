import pandas as pd
import pytest
from guanlan_v2.screen import model_registry as reg


def _ranking_df():
    codes = [f"SZ{300000 + i:06d}" for i in range(200)]
    return pd.DataFrame({"code": codes, "date": "2026-06-19",
                         "lgb_pct": [i / 199 for i in range(200)]})


def test_save_fills_provenance_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    reg.save_variant("m_test1", _ranking_df(), {"id": "m_test1", "name": "变体1"})
    m = reg.variant_meta("m_test1")
    assert m["source"] == "workshop"
    assert m["kind"] == "v4-lgb"
    assert m["retrainable"] is False
    assert m["recipe"] == {}


def test_save_keeps_explicit_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    reg.save_variant("m_wf1", _ranking_df(),
                     {"id": "m_wf1", "name": "工作流模型", "source": "workflow",
                      "kind": "lightgbm", "recipe": {"features": ["close/Ref(close,5)"]},
                      "retrainable": True})
    m = reg.variant_meta("m_wf1")
    assert m["source"] == "workflow"
    assert m["kind"] == "lightgbm"
    assert m["retrainable"] is True


def test_list_variants_normalizes_old_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    d = tmp_path / "m_old"; d.mkdir(parents=True)
    (d / "v4_ranking.parquet").write_bytes(b"")
    (d / "meta.json").write_text('{"id":"m_old","name":"老变体","oos_ic":0.01}', encoding="utf-8")
    rows = reg.list_variants()
    old = [r for r in rows if r["id"] == "m_old"][0]
    assert old["source"] == "workshop" and old["kind"] == "v4-lgb"


def test_validate_ranking_rejects_missing_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    bad = pd.DataFrame({"code": ["SZ300001"], "date": ["2026-06-19"]})  # 缺 lgb_pct
    with pytest.raises(ValueError, match="lgb_pct"):
        reg.save_variant("m_bad", bad, {"id": "m_bad", "name": "坏"})


def test_validate_ranking_rejects_thin_cross_section(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    thin = pd.DataFrame({"code": ["SZ300001", "SZ300002"], "date": "2026-06-19",
                         "lgb_pct": [0.1, 0.9]})  # 截面仅 2 票 < 阈值
    with pytest.raises(ValueError, match="截面"):
        reg.save_variant("m_thin", thin, {"id": "m_thin", "name": "薄"})


def test_validate_ranking_rejects_non_dataframe(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    with pytest.raises(ValueError, match="DataFrame"):
        reg.save_variant("m_none", None, {"id": "m_none", "name": "空"})


def test_validate_ranking_rejects_empty_df(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path)
    empty = pd.DataFrame({"code": [], "date": [], "lgb_pct": []})
    with pytest.raises(ValueError, match="空"):
        reg.save_variant("m_empty", empty, {"id": "m_empty", "name": "空"})
