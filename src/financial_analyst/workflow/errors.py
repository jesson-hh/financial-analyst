"""Workflow 异常树 — 单根 WorkflowError 便于统一捕获。"""
from __future__ import annotations
from typing import List

class WorkflowError(Exception):
    """所有 workflow 子系统异常的根。"""

class NodeNotFoundError(WorkflowError):
    """registry 找不到 node type."""
    def __init__(self, node_type: str):
        super().__init__(f"node type not registered: {node_type!r}")
        self.node_type = node_type

class CycleError(WorkflowError):
    """topo sort 发现环."""
    def __init__(self, cycle_nodes: List[str]):
        super().__init__(f"cycle detected involving nodes: {cycle_nodes}")
        self.cycle_nodes = cycle_nodes

class NodeExecutionError(WorkflowError):
    """节点 compute 异常 (包裹原异常)."""
    def __init__(self, node_id: str, original: Exception):
        super().__init__(f"node {node_id!r} execution failed: {type(original).__name__}: {original}")
        self.node_id = node_id
        self.original = original
