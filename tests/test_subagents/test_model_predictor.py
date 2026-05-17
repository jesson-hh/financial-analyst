import pytest
from unittest.mock import patch
from financial_analyst.agent.tier1.model_predictor import ModelPredictor
from financial_analyst.models import ModelRegistry, BaseModel


class StubModel(BaseModel):
    def predict(self, code, asof):
        return {"score": 0.7, "rank_pct": 0.82}

    def metadata(self):
        return {"name": "stub", "version": "0.1"}


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    ModelRegistry.clear()
    from financial_analyst.models.lgb_momentum import LGBMomentumModel
    if "lgb_momentum" not in ModelRegistry.names():
        ModelRegistry.register("lgb_momentum", LGBMomentumModel)


@pytest.mark.asyncio
async def test_model_predictor_runs_all_registered(tmp_path):
    ModelRegistry.clear()
    ModelRegistry.register("stub", StubModel)
    agent = ModelPredictor(memory_root=tmp_path)
    result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert "stub" in result.output.per_model
    assert result.output.per_model["stub"]["score"] == 0.7


@pytest.mark.asyncio
async def test_model_predictor_consensus_rank_pct(tmp_path):
    ModelRegistry.clear()
    ModelRegistry.register("stub", StubModel)
    agent = ModelPredictor(memory_root=tmp_path)
    result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert result.output.consensus_rank_pct == pytest.approx(0.82)


@pytest.mark.asyncio
async def test_model_predictor_empty_registry_uses_default(tmp_path):
    ModelRegistry.clear()
    agent = ModelPredictor(memory_root=tmp_path)
    result = await agent.run({"code": "SH600519", "asof_date": "2026-05-17"})
    assert result.ok is True
    assert result.output.per_model == {}
    assert result.output.consensus_rank_pct == pytest.approx(0.5)
