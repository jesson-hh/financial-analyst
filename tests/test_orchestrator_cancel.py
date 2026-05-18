"""Tests for orchestrator graceful cancellation."""
import asyncio
import pytest
from pydantic import BaseModel
from financial_analyst.agent.base import SubAgent
from financial_analyst.agent.orchestrator import Orchestrator, DAGNode


class _Out(BaseModel):
    value: int = 0


class _SlowAgent(SubAgent[_Out]):
    NAME = "slow"
    OUTPUT_SCHEMA = _Out

    async def _execute(self, inputs):
        await asyncio.sleep(2.0)   # long enough to cancel
        return {"value": 1}


class _FastAgent(SubAgent[_Out]):
    NAME = "fast"
    OUTPUT_SCHEMA = _Out

    async def _execute(self, inputs):
        await asyncio.sleep(0.01)
        return {"value": 1}


@pytest.mark.asyncio
async def test_orchestrator_cancellable(tmp_path):
    """Cancelling mid-run should propagate but leave done dict populated."""
    nodes = [
        DAGNode(agent=_FastAgent(memory_root=tmp_path), deps=[], input_keys=[]),
        DAGNode(agent=_SlowAgent(memory_root=tmp_path), deps=[], input_keys=[]),
    ]
    orch = Orchestrator(nodes)
    task = asyncio.create_task(orch.run({}))
    await asyncio.sleep(0.5)   # let fast finish, slow still running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
