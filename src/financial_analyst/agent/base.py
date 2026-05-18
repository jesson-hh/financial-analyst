from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Generic, Optional, Type, TypeVar
from pydantic import BaseModel, ValidationError
from financial_analyst.agent.memory import AgentMemory

if TYPE_CHECKING:
    from financial_analyst.agent.memory_index import MemoryIndex

TOutput = TypeVar("TOutput", bound=BaseModel)


class SubAgentResult(BaseModel, Generic[TOutput]):
    ok: bool
    agent_name: str
    output: Optional[TOutput] = None
    error: Optional[str] = None
    elapsed_seconds: float = 0.0


class SubAgent(ABC, Generic[TOutput]):
    NAME: str = ""
    OUTPUT_SCHEMA: Type[BaseModel] = None  # subclass MUST set

    def __init__(
        self,
        memory_root: Path,
        borrows: Optional[list[str]] = None,
        index: Optional["MemoryIndex"] = None,
    ):
        if not self.NAME:
            raise ValueError(f"{type(self).__name__}.NAME must be set")
        if self.OUTPUT_SCHEMA is None:
            raise ValueError(f"{type(self).__name__}.OUTPUT_SCHEMA must be set")
        self.memory = AgentMemory(self.NAME, memory_root, borrows=borrows, index=index)

    @abstractmethod
    async def _execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]: ...

    async def run(self, inputs: Dict[str, Any]) -> SubAgentResult:
        import time
        start = time.time()
        try:
            raw = await self._execute(inputs)
            output = self.OUTPUT_SCHEMA(**raw)
            return SubAgentResult(
                ok=True,
                agent_name=self.NAME,
                output=output,
                elapsed_seconds=time.time() - start,
            )
        except ValidationError as ve:
            return SubAgentResult(
                ok=False,
                agent_name=self.NAME,
                error=str(ve),
                elapsed_seconds=time.time() - start,
            )
        except Exception as e:
            return SubAgentResult(
                ok=False,
                agent_name=self.NAME,
                error=f"{type(e).__name__}: {e}",
                elapsed_seconds=time.time() - start,
            )
