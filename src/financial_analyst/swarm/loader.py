"""Load a swarm preset YAML and build a list of DAGNodes from the registry."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import yaml

from financial_analyst.agent.orchestrator import DAGNode
from financial_analyst.agent.registry import SubAgentRegistry

if TYPE_CHECKING:
    from financial_analyst.agent.memory_index import MemoryIndex

# Default location: <repo-root>/config/swarm/
# Path: loader.py is at src/financial_analyst/swarm/loader.py
# parents[0]=swarm  [1]=financial_analyst  [2]=src  [3]=<repo-root>
PRESET_DIR = Path(__file__).resolve().parents[3] / "config" / "swarm"


def _accepts_kwarg(cls, kwarg: str) -> bool:
    """Return True if *cls.__init__* accepts *kwarg* as a named parameter."""
    try:
        sig = inspect.signature(cls)
        return kwarg in sig.parameters
    except (TypeError, ValueError):
        return False


def load_preset(
    name: str,
    memory_root: Path,
    preset_dir: Optional[Path] = None,
    memory_index: Optional["MemoryIndex"] = None,
) -> List[DAGNode]:
    """Parse *name*.yaml and instantiate each agent from SubAgentRegistry.

    Parameters
    ----------
    name:
        Preset name without extension, e.g. ``"stock-deep-dive"``.
    memory_root:
        Passed to every agent constructor as ``memory_root``.
    preset_dir:
        Override the directory that holds preset YAML files (useful in tests).
    memory_index:
        Optional shared :class:`MemoryIndex` instance. Agents whose YAML entry
        specifies ``memory_mode: retrieval`` will receive this index so their
        ``AgentMemory`` uses FTS5 retrieval instead of loading all files.
        When ``None`` (or when the agent has no ``memory_mode`` key / ``full``),
        full-load behaviour is preserved (v0.1 compatible).

    Returns
    -------
    List[DAGNode]
        Ordered exactly as declared in the YAML ``agents`` list.
    """
    pd = preset_dir or PRESET_DIR
    path = pd / f"{name}.yaml"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))

    nodes: List[DAGNode] = []
    for entry in spec["agents"]:
        agent_name: str = entry["name"]
        borrows: list = entry.get("borrows_memory", [])
        memory_mode: str = entry.get("memory_mode", "full")

        # Collect kwargs accepted by this agent's constructor
        kwargs: dict = {}
        agent_cls = SubAgentRegistry._registry.get(agent_name)

        if borrows and agent_cls is not None and _accepts_kwarg(agent_cls, "borrows"):
            kwargs["borrows"] = borrows

        if (
            memory_mode == "retrieval"
            and memory_index is not None
            and agent_cls is not None
            and _accepts_kwarg(agent_cls, "index")
        ):
            kwargs["index"] = memory_index

        try:
            agent = SubAgentRegistry.build(agent_name, memory_root=memory_root, **kwargs)
        except Exception:
            agent = SubAgentRegistry.build(agent_name, memory_root=memory_root)

        nodes.append(
            DAGNode(
                agent=agent,
                deps=entry.get("deps", []),
                input_keys=entry.get("input_keys", []),
            )
        )

    return nodes
