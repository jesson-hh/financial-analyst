from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from financial_analyst.agent.base import SubAgent, SubAgentResult


@dataclass
class DAGNode:
    agent: SubAgent
    deps: List[str] = field(default_factory=list)
    input_keys: List[str] = field(default_factory=list)


class Orchestrator:
    def __init__(self, nodes: List[DAGNode], on_event: Optional[Callable[[str, Dict], None]] = None):
        self.nodes = {n.agent.NAME: n for n in nodes}
        self.on_event = on_event or (lambda evt, data: None)

    def _ready(self, name: str, done: Dict[str, SubAgentResult]) -> bool:
        node = self.nodes[name]
        return all(dep in done and done[dep].ok for dep in node.deps)

    def _build_inputs(self, name: str, base: Dict[str, Any], done: Dict[str, SubAgentResult]) -> Dict[str, Any]:
        node = self.nodes[name]
        inputs: Dict[str, Any] = {}
        for k in node.input_keys:
            if k in done and done[k].ok:
                output = done[k].output
                fields = type(output).model_fields
                # Unwrap single-field models to their scalar value for ergonomic agent chaining
                if len(fields) == 1:
                    (field_name,) = fields.keys()
                    inputs[k] = getattr(output, field_name)
                else:
                    inputs[k] = output.model_dump()
            elif k in base:
                inputs[k] = base[k]
        return inputs

    async def run(self, base_inputs: Dict[str, Any]) -> Dict[str, SubAgentResult]:
        done: Dict[str, SubAgentResult] = {}
        remaining = set(self.nodes.keys())

        while remaining:
            wave = [n for n in remaining if self._ready(n, done)]
            if not wave:
                blocked = remaining - {n for n in remaining if self._ready(n, done)}
                for name in blocked:
                    done[name] = SubAgentResult(
                        ok=False, agent_name=name,
                        error="upstream dependency failed",
                    )
                break

            self.on_event("wave_start", {"agents": wave})
            coros = [self.nodes[n].agent.run(self._build_inputs(n, base_inputs, done)) for n in wave]
            results = await asyncio.gather(*coros)
            for name, result in zip(wave, results):
                done[name] = result
                self.on_event("agent_done", {"agent": name, "ok": result.ok, "elapsed": result.elapsed_seconds})
                remaining.discard(name)

        return done
