# tests/test_model_registry.py
import pandas as pd, pytest
from guanlan_v2.screen import model_registry as reg

_DF = pd.DataFrame({"code": ["SH600519"], "lgb_score": [1.0], "lgb_pct": [0.9],
                    "lgb_rank": [1], "v4_total": [5], "v4_layer": ["大盘"], "date": ["2026-06-17"]})

def test_save_list_get_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    reg.save_variant("m_a", _DF, {"id": "m_a", "name": "甲", "oos_ic": 0.05})
    reg.save_variant("m_b", _DF, {"id": "m_b", "name": "乙", "oos_ic": 0.02})
    assert [v["id"] for v in reg.list_variants()] == ["m_a", "m_b"]    # oos_ic 降序
    assert reg.variant_meta("m_a")["name"] == "甲"
    assert reg.variant_ranking_path("m_a").exists()
    reg.delete_variant("m_a")
    assert [v["id"] for v in reg.list_variants()] == ["m_b"]

def test_delete_prod_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    with pytest.raises(ValueError):
        reg.delete_variant("prod")
