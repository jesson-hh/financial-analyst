import pytest
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.agent.registry import SubAgentRegistry

class FakeOut(BaseModel):
    x: int

class FakeAgent(SubAgent[FakeOut]):
    NAME = "fake"
    OUTPUT_SCHEMA = FakeOut
    async def _execute(self, inputs):
        return {"x": 1}

def test_register_and_build(tmp_path):
    SubAgentRegistry.clear()
    SubAgentRegistry.register("fake", FakeAgent)
    agent = SubAgentRegistry.build("fake", memory_root=tmp_path)
    assert agent.NAME == "fake"

def test_unknown_raises():
    SubAgentRegistry.clear()
    with pytest.raises(KeyError):
        SubAgentRegistry.build("missing", memory_root="/tmp")

def test_duplicate_register_raises(tmp_path):
    SubAgentRegistry.clear()
    SubAgentRegistry.register("fake", FakeAgent)
    with pytest.raises(ValueError):
        SubAgentRegistry.register("fake", FakeAgent)
