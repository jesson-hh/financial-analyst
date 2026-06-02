"""Workflow Pydantic schema.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §3.

JSON 形状是契约 — Python 类名 / 模块路径变动不应破坏已保存的 workflow 文件。
所有跨进程 / 跨版本 序列化用 ``model_dump(mode='json')`` (Enum 落字符串)。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NodeStatus(str, Enum):
    """节点 / workflow run 状态. 字符串 Enum, JSON 序列化为字面字符串."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Node(BaseModel):
    """Workflow 中的一个节点.

    - ``id``: workflow 内唯一, 边的端点 / inputs 引用都用这个。
    - ``type``: 注册表查找键, 形如 ``"data.constant_universe"``。
    - ``params``: 节点参数, runtime 转给 ``RegisteredNode.params_model`` 校验。
    - ``inputs``: ``{input_name: "<upstream_node_id>.<output_name>"}``。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)


class Edge(BaseModel):
    """显式边 (与 ``Node.inputs`` 并存, runner 合并).

    JSON 字段名 ``from`` (Python 关键字, 用 alias), Python 属性 ``from_``.
    populate_by_name=True 让 ``Edge(from_=...)`` 也能工作。
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    from_: str = Field(..., alias="from", min_length=1)
    to: str = Field(..., min_length=1)


class Workflow(BaseModel):
    """一条 workflow 模板.

    Phase 0 同时支持 ``Node.inputs`` 直接引用上游, 或显式 ``edges`` 列表 (画布友好)。
    Runner 校验时合并两边构造 DAG。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    version: int = 1
    nodes: list[Node] = Field(..., min_length=1)
    edges: list[Edge] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class NodeRun(BaseModel):
    """一次节点执行的不可变记录, append 进 ``run_log.jsonl``.

    跨进程读: 必须用 ``model_dump(mode='json')`` 才能让 Enum 落字符串.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: str
    node_id: str
    node_type: str
    status: NodeStatus
    input_hash: str | None = None
    output_artifact_uri: str | None = None
    started_at: str  # ISO 8601 UTC
    ended_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None  # 失败时 "{ExcCls}: {msg}", 不含 traceback


class RunResult(BaseModel):
    """``WorkflowRunner.run()`` 返回值."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: str
    status: NodeStatus
    node_runs: list[NodeRun] = Field(default_factory=list)
    artifacts_root: str
    duration_ms: int | None = None  # 整个 run 耗时, runner 填; 旧 RunResult 兼容默认 None.
