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
    # soft_deps ⊆ deps — 软依赖只需「跑完」(done)不需「成功」(ok)。用于纯上下文 agent:
    # 它们失败不应拖垮终端节点(如 report-writer)。失败的软依赖在 _build_inputs 里自然
    # 不贡献输入(那里已要求 done[k].ok),于是下游优雅降级出报告,而非整份夭折。
    soft_deps: List[str] = field(default_factory=list)


class Orchestrator:
    def __init__(self, nodes: List[DAGNode], on_event: Optional[Callable[[str, Dict], None]] = None):
        self.nodes = {n.agent.NAME: n for n in nodes}
        self.on_event = on_event or (lambda evt, data: None)

    def _ready(self, name: str, done: Dict[str, SubAgentResult]) -> bool:
        node = self.nodes[name]
        soft = set(node.soft_deps)
        for dep in node.deps:
            if dep not in done:
                return False              # 任何依赖(软/硬)都必须先跑完
            if dep not in soft and not done[dep].ok:
                return False              # 硬依赖还必须成功;软依赖失败也放行
        return True

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
            try:
                results = await asyncio.gather(*coros)
            except asyncio.CancelledError:
                # Mark all wave agents as cancelled, exit early with partial results
                for name in wave:
                    if name not in done:
                        done[name] = SubAgentResult(
                            ok=False, agent_name=name,
                            error="cancelled by user (KeyboardInterrupt / CancelledError)",
                        )
                # Also mark all not-yet-started as cancelled
                for name in remaining - set(wave):
                    done[name] = SubAgentResult(
                        ok=False, agent_name=name,
                        error="cancelled before start",
                    )
                self.on_event("cancelled", {"completed": len([d for d in done.values() if d.ok])})
                raise  # re-raise so the caller knows the user cancelled

            for name, result in zip(wave, results):
                done[name] = result
                self.on_event("agent_done", {"agent": name, "ok": result.ok, "elapsed": result.elapsed_seconds})
                remaining.discard(name)

        return done
