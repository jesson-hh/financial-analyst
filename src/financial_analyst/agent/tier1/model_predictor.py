from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel as PydModel

from financial_analyst.agent.base import SubAgent
from financial_analyst.models.registry import ModelRegistry


class ModelOutput(PydModel):
    code: str
    asof_date: str
    per_model: Dict[str, Dict[str, float]]
    consensus_rank_pct: float


class ModelPredictor(SubAgent[ModelOutput]):
    NAME = "model-predictor"
    OUTPUT_SCHEMA = ModelOutput

    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        code, asof = inputs["code"], inputs["asof_date"]
        per_model = ModelRegistry.predict_all(code, asof)
        ranks = [
            v.get("rank_pct", 0.5)
            for v in per_model.values()
            if v.get("rank_pct") == v.get("rank_pct")  # NaN check
        ]
        consensus = float(sum(ranks) / len(ranks)) if ranks else 0.5
        return {
            "code": code,
            "asof_date": asof,
            "per_model": per_model,
            "consensus_rank_pct": consensus,
        }
