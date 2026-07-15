"""guanlan_v2.orchestration — 通用编排内核的契约层(Phase 1)。

纯离线、零运行时行为:严格/带版本/不可变的 Pydantic v2 契约模型 + 确定性
语义/审计摘要。后续任务(enums/refs/registry/data/…)全部继承本包 Task 1
建立的 ``ContractModel`` / ``DigestModel``,并调用 ``canonical_json`` /
``content_digest`` / ``audit_digest``。

Task 13 之前本 ``__init__`` 只作包标记;公共导出在 Task 13 统一冻结审阅。
"""
from __future__ import annotations

__all__: list[str] = []
