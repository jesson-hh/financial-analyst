"""QuantFlow Phase 0 — Workflow framework.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md.

Hybrid 架构:
- 实现层 = Python 函数 + ``@node`` 装饰器 (函数是真源)
- 存储层 = JSON / YAML workflow 文件 (通过字符串 ``type`` 引用节点)
- 两层通过全局 ``NodeRegistry`` 解耦

Phase 0 范围: schema + registry. Runner / artifacts / run_log 留 Phase 0 后续 PR。
"""

from financial_analyst.workflow.artifacts import ArtifactStore
from financial_analyst.workflow.errors import (
    CycleError,
    NodeExecutionError,
    NodeNotFoundError,
    WorkflowError,
)
from financial_analyst.workflow.protocols import (
    ArtifactStoreProto,
    Payload,
    RunLogProto,
)
from financial_analyst.workflow.registry import (
    NodeRegistry,
    RegisteredNode,
    node,
)
from financial_analyst.workflow.run_log import RunLog
from financial_analyst.workflow.runner import WorkflowRunner
from financial_analyst.workflow.schema import (
    Edge,
    Node,
    NodeRun,
    NodeStatus,
    RunResult,
    Workflow,
)

__all__ = [
    # schema
    "Workflow",
    "Node",
    "Edge",
    "NodeRun",
    "NodeStatus",
    "RunResult",
    # registry
    "NodeRegistry",
    "RegisteredNode",
    "node",
    # artifacts
    "ArtifactStore",
    # run log
    "RunLog",
    # runner
    "WorkflowRunner",
    # protocols (单一来源)
    "ArtifactStoreProto",
    "RunLogProto",
    "Payload",
    # errors (单一来源)
    "WorkflowError",
    "NodeNotFoundError",
    "CycleError",
    "NodeExecutionError",
]
