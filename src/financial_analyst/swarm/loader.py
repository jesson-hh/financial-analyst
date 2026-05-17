"""Load a swarm preset YAML and build a list of DAGNodes from the registry."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import List, Optional

import yaml

from financial_analyst.agent.orchestrator import DAGNode
from financial_analyst.agent.registry import SubAgentRegistry

# Default location: <repo-root>/config/swarm/
# Path: loader.py is at src/financial_analyst/swarm/loader.py
# parents[0]=swarm  [1]=financial_analyst  [2]=src  [3]=<repo-root>
PRESET_DIR = Path(__file__).resolve().parents[3] / "config" / "swarm"


def load_preset(
    name: str,
    memory_root: Path,
    preset_dir: Optional[Path] = None,
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

        # Build agent — pass `borrows` only when the constructor accepts it
        try:
            sig = inspect.signature(SubAgentRegistry._registry[agent_name])
            if "borrows" in sig.parameters and borrows:
                agent = SubAgentRegistry.build(
                    agent_name, memory_root=memory_root, borrows=borrows
                )
            else:
                agent = SubAgentRegistry.build(agent_name, memory_root=memory_root)
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
