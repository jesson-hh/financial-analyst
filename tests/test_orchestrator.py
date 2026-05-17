import asyncio
import pytest
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.agent.orchestrator import Orchestrator, DAGNode

class IntOut(BaseModel):
    value: int

class AddOne(SubAgent[IntOut]):
    NAME = "add-one"
    OUTPUT_SCHEMA = IntOut
    async def _execute(self, inputs):
        return {"value": inputs.get("v", 0) + 1}

class TimesTwo(SubAgent[IntOut]):
    NAME = "times-two"
    OUTPUT_SCHEMA = IntOut
    async def _execute(self, inputs):
        return {"value": inputs.get("add-one", 0) * 2}

@pytest.mark.asyncio
async def test_orchestrator_runs_dag(tmp_path):
    nodes = [
        DAGNode(agent=AddOne(memory_root=tmp_path), deps=[], input_keys=["v"]),
        DAGNode(agent=TimesTwo(memory_root=tmp_path), deps=["add-one"], input_keys=["add-one"]),
    ]
    orch = Orchestrator(nodes)
    results = await orch.run({"v": 3})
    assert results["add-one"].output.value == 4
    assert results["times-two"].output.value == 8

@pytest.mark.asyncio
async def test_orchestrator_parallel_tier(tmp_path):
    nodes = [
        DAGNode(agent=AddOne(memory_root=tmp_path), deps=[], input_keys=["v"]),
        DAGNode(agent=TimesTwo(memory_root=tmp_path), deps=[], input_keys=[]),
    ]
    orch = Orchestrator(nodes)
    results = await orch.run({"v": 5, "add-one": 10})
    assert results["add-one"].ok is True
    assert results["times-two"].ok is True
