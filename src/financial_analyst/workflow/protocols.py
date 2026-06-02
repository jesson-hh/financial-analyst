"""Workflow 子系统的协议接口 — 单一来源, 任何 ArtifactStore/RunLog 实现必须满足。

为什么需要这个文件: Phase 0 第一轮 3 个 plumbing agent 并行写 runner/artifacts/run_log,
互相不知道对方协议长什么样, 最终 runner 的 ArtifactStoreProto (4 参) 与 artifacts.py
的 write() (3 参) 不兼容, e2e 必须写桥接 — 痛苦的教训。"""
from __future__ import annotations
from pathlib import Path
from typing import Protocol, Union, runtime_checkable
import pandas as pd

# 节点输出 payload 允许类型
Payload = Union[dict, list, str, int, float, bool, None, pd.DataFrame, pd.Series]

@runtime_checkable
class ArtifactStoreProto(Protocol):
    """节点产出的 artifact 落盘/读取协议。

    URI 一律返 POSIX 相对路径字符串 (如 'workflow_runs/run_x/nodes/foo/output.json'),
    便于 UI/数据库引用。output_name 默认 'output', 多输出节点可显式区分
    ('signal'/'positions' 等)。"""
    def write(self, run_id: str, node_id: str, output_name: str, payload: Payload) -> str: ...
    def read(self, run_id: str, node_id: str, output_name: str = "output") -> Payload: ...
    def exists(self, run_id: str, node_id: str, output_name: str = "output") -> bool: ...
    @property
    def root(self) -> Path: ...

@runtime_checkable
class RunLogProto(Protocol):
    """节点运行日志 JSONL 协议. 同 path 跨实例锁共享 (实现责任)."""
    def append(self, node_run) -> None: ...   # node_run: NodeRun (schema.py)
    def read_all(self) -> list: ...
    def latest_status(self, node_id: str): ...
