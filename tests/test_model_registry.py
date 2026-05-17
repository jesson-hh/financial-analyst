import pytest
from financial_analyst.models.base import BaseModel
from financial_analyst.models.registry import ModelRegistry

class FakeModel(BaseModel):
    def predict(self, code, asof):
        return {"score": 0.5}
    def metadata(self):
        return {"name": "fake", "version": "0.1"}

def test_register_and_get():
    ModelRegistry.clear()
    ModelRegistry.register("fake", FakeModel)
    assert "fake" in ModelRegistry.names()
    inst = ModelRegistry.get_instance("fake")
    assert isinstance(inst, FakeModel)

def test_predict_all():
    ModelRegistry.clear()
    ModelRegistry.register("fake", FakeModel)
    results = ModelRegistry.predict_all("SH600519", "2026-05-17")
    assert results["fake"]["score"] == 0.5

def test_base_model_is_abstract():
    with pytest.raises(TypeError):
        BaseModel()

def test_duplicate_register_raises():
    ModelRegistry.clear()
    ModelRegistry.register("fake", FakeModel)
    with pytest.raises(ValueError, match="already registered"):
        ModelRegistry.register("fake", FakeModel)
