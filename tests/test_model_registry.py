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

def test_load_v4_ranking_by_model(tmp_path, monkeypatch):
    from guanlan_v2.strategy import ranking as R
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    reg.save_variant("m_x", _DF, {"id": "m_x"})
    assert list(R.load_v4_ranking(model_id="m_x")["code"]) == ["SH600519"]
    assert R.ranking_date(model_id="m_x") == "2026-06-17"
    with pytest.raises(FileNotFoundError):
        R.load_v4_ranking(model_id="nope")


def _stub_variant(root, vid):
    """造一个最小变体目录(只需 ranking 文件存在;set/get_default 只看 .exists())。"""
    d = root / vid
    d.mkdir(parents=True, exist_ok=True)
    (d / "v4_ranking.parquet").write_bytes(b"stub")
    return d


def test_default_model_set_get_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    _stub_variant(tmp_path / "models", "m_a")
    assert reg.get_default_model() is None            # 缺省 = None(=prod)
    reg.set_default_model("m_a")
    assert reg.get_default_model() == "m_a"
    reg.set_default_model("prod")                       # "prod" = 清除
    assert reg.get_default_model() is None
    reg.set_default_model("m_a")
    reg.set_default_model(None)                         # None = 清除
    assert reg.get_default_model() is None


def test_set_default_unknown_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    with pytest.raises(ValueError):
        reg.set_default_model("m_nope")                # 变体不存在 → 诚实失败


def test_get_default_degrades_when_variant_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "MODELS_DIR", tmp_path / "models")
    _stub_variant(tmp_path / "models", "m_a")
    reg.set_default_model("m_a")
    reg.delete_variant("m_a")                           # 删默认变体
    assert reg.get_default_model() is None              # 指针自愈 + 被清
    assert not (tmp_path / "models" / "_default.json").exists()
