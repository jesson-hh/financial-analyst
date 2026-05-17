import pytest
from pydantic import BaseModel as PydModel
from financial_analyst.agent.base import SubAgent, SubAgentResult

class FakeOutput(PydModel):
    value: int

class FakeAgent(SubAgent[FakeOutput]):
    NAME = "fake-agent"
    OUTPUT_SCHEMA = FakeOutput

    async def _execute(self, inputs):
        return {"value": inputs.get("v", 0)}

@pytest.mark.asyncio
async def test_subagent_validates_output(tmp_path):
    agent = FakeAgent(memory_root=tmp_path)
    result = await agent.run({"v": 42})
    assert result.ok is True
    assert result.output.value == 42

@pytest.mark.asyncio
async def test_subagent_rejects_invalid_output(tmp_path):
    class BadAgent(SubAgent[FakeOutput]):
        NAME = "bad"
        OUTPUT_SCHEMA = FakeOutput
        async def _execute(self, inputs):
            return {"value": "not-an-int"}
    agent = BadAgent(memory_root=tmp_path)
    result = await agent.run({})
    assert result.ok is False
    assert "value" in result.error.lower() or "int" in result.error.lower()

@pytest.mark.asyncio
async def test_subagent_loads_memory(tmp_path):
    (tmp_path / "fake-agent").mkdir()
    (tmp_path / "fake-agent" / "rules.md").write_text("rule X")
    agent = FakeAgent(memory_root=tmp_path)
    assert "rule X" in agent.memory.load_all()
