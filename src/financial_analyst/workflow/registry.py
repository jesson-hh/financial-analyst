"""NodeRegistry + @node 装饰器.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §4.

铁律:
- ``@node`` 装饰器签名 = ``@node('factor.rev20', ...)`` 或 ``@node(type='factor.rev20', ...)``。
  ``type`` 是位置必填, ``@node()`` 直接 TypeError (Python 签名层强制).
- 装饰器**禁止**从 ``func.__name__`` 自动取 type — 这是 Python 路径与 workflow 文件
  解耦的硬约束。refactor 改 Python 路径 / 函数名不会破坏已保存的 workflow JSON。
- 重复注册同 ``type`` 抛 ``ValueError``。
- 缺失 ``type`` 抛 ``NodeNotFoundError`` (继承 ``WorkflowError``, 不再继承 KeyError).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

# NodeNotFoundError 移到 errors.py (单一来源, 与 WorkflowError 异常树统一).
# registry.py 仍 re-export 它, 老代码 ``from financial_analyst.workflow.registry
# import NodeNotFoundError`` 继续可用.
from financial_analyst.workflow.errors import NodeNotFoundError


@dataclass(frozen=True)
class RegisteredNode:
    """一个注册到 ``NodeRegistry`` 的节点.

    Frozen dataclass = 注册后形状不可变 (字段值通过 dict 缓存间接可修改, 但顶层
    ``type`` / ``compute`` 等替换被禁止). 这让 ``NodeRegistry.get()`` 返回的
    对象语义稳定。

    SP-W2A 加 ``group`` / ``tag`` 两字段 — 前端工具栏按 ``group`` 分组, Copilot 按
    ``tag`` 过滤候选节点 (借 PandaAI FeatureTag 思路, 但用 list 支持多 tag).
    """

    type: str  # 字符串身份, e.g. "data.constant_universe"
    compute: Callable[..., Any]  # 装饰的原函数
    params_model: type[BaseModel] | None = None  # 参数校验
    outputs_model: type[BaseModel] | None = None  # 输出形状校验
    risk: str = "normal"  # "normal" | "intraday" | "advice" | "live"
    pit: bool = False  # 是否要求 PIT 输入 (Phase 0 仅记录)
    group: str = "misc"  # 'data' | 'factor' | 'eval' | 'agent' | 'risk' | 'execution' | 'review' | 'demo' | 'misc'
    tag: list[str] = field(default_factory=list)  # e.g. ['backtest','factor','signal','trade']
    meta: dict[str, Any] = field(default_factory=dict)


class NodeRegistry:
    """全局节点注册表 (单例形态).

    用类属性 dict 而非模块级 dict, 让 ``NodeRegistry`` 名字本身可作为 API 入口。
    ``unregister`` 是测试 / 热重载用接口, 不在公开 README, 但保留可见。
    """

    _registry: dict[str, RegisteredNode] = {}

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    @classmethod
    def register(cls, registered: RegisteredNode) -> None:
        """注册节点. 重复 type 抛 ValueError."""
        if not registered.type:
            raise ValueError("RegisteredNode.type 不能为空")
        if registered.type in cls._registry:
            raise ValueError(
                f"Node type {registered.type!r} 已注册. "
                f"如需替换请先调 NodeRegistry.unregister({registered.type!r})."
            )
        cls._registry[registered.type] = registered

    @classmethod
    def unregister(cls, type: str) -> None:
        """移除注册. 缺失抛 KeyError."""
        if type not in cls._registry:
            raise KeyError(f"Node type {type!r} 未注册, 无法移除")
        del cls._registry[type]

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    @classmethod
    def get(cls, type: str) -> RegisteredNode:
        """按字符串 type 取出. 缺失抛 NodeNotFoundError (继承 WorkflowError)."""
        if type not in cls._registry:
            raise NodeNotFoundError(type)
        return cls._registry[type]

    @classmethod
    def list(cls) -> dict[str, RegisteredNode]:
        """返回拷贝, 防外部修改 _registry."""
        return dict(cls._registry)

    # 兼容 spec §4.3 命名 (all = list, 但 ``all`` 是 builtin, 用 ``list`` 主推)
    @classmethod
    def all(cls) -> dict[str, RegisteredNode]:
        return cls.list()

    # ------------------------------------------------------------------
    # SP-W2A: 按 group / tag 过滤
    # ------------------------------------------------------------------
    @classmethod
    def list_by_group(cls, group: str) -> list[RegisteredNode]:
        """返回 ``group`` 字段精确匹配的节点列表 (按 type 字典序排)."""
        out = [r for r in cls._registry.values() if r.group == group]
        return sorted(out, key=lambda r: r.type)

    @classmethod
    def list_by_tag(cls, tag: str) -> list[RegisteredNode]:
        """返回 ``tag`` 在该节点 tag list 中的节点列表 (按 type 字典序排).

        允许多 tag 节点同时出现在多个 tag 过滤结果中.
        """
        out = [r for r in cls._registry.values() if tag in (r.tag or [])]
        return sorted(out, key=lambda r: r.type)


def node(
    type: str,
    *,
    params_model: type[BaseModel] | None = None,
    outputs_model: type[BaseModel] | None = None,
    risk: str = "normal",
    pit: bool = False,
    group: str = "misc",
    tag: list[str] | None = None,
    description: str = "",
    **meta: Any,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册一个 workflow 节点.

    ``type`` 是**位置必填**字符串. 装饰器**禁止**默认取 ``func.__name__`` —
    这是 Python 路径与 workflow 文件解耦的硬约束.

    被装饰的函数签名: ``compute(params, inputs) -> outputs``

    - ``params``: dict (若 ``params_model != None``, runner 会先 ``model_validate``)
    - ``inputs``: dict[str, Any] (上游节点产出, key 与 ``Node.inputs`` 对齐)
    - ``outputs``: dict / DataFrame / Series / scalar (若 ``outputs_model != None``,
      runner 会用它校验形状)
    - ``description``: 人类可读的节点说明, 落到 ``RegisteredNode.meta['description']``,
      UI / 文档可展示。
    - ``group``: 工具栏分组 (SP-W2A). 'data' | 'factor' | 'eval' | 'agent' | 'risk'
      | 'execution' | 'review' | 'demo' | 'misc'. 默认 'misc'.
    - ``tag``: feature tag 列表 (SP-W2A). 多 tag 允许同节点出现在多个过滤结果中.
      默认空列表 (向后兼容已注册节点).

    Examples
    --------
    >>> @node("data.constant_universe", params_model=UniverseParams,
    ...       group="data", tag=["data"])
    ... def constant_universe(params, inputs):
    ...     return {"codes": params["codes"], "n": len(params["codes"])}

    Raises
    ------
    TypeError
        ``@node()`` 不传 type — Python 签名层直接拒.
    ValueError
        ``type`` 为 None / 空字符串 / 已注册 — 由 ``NodeRegistry.register`` 兜底.
    """
    # type 必须由 Python 签名层强制 (位置必填). 不再做 runtime "if not type" 检查,
    # 空 / None 一律由 RegisteredNode + NodeRegistry.register 兜底:
    #   - type=None  → register(): "RegisteredNode.type 不能为空" → ValueError
    #   - type=""    → 同上
    # 重复 type → register() 抛 ValueError
    type_key = type  # 防内层闭包遮蔽

    # description 进 meta, 给 UI / 文档用
    meta_dict: dict[str, Any] = dict(meta)
    if description:
        meta_dict["description"] = description

    # tag 默认空列表 — 不用可变默认参数 (Python 经典坑)
    tag_list: list[str] = list(tag) if tag else []

    def _decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        registered = RegisteredNode(
            type=type_key,
            compute=func,
            params_model=params_model,
            outputs_model=outputs_model,
            risk=risk,
            pit=pit,
            group=group,
            tag=tag_list,
            meta=meta_dict,
        )
        NodeRegistry.register(registered)
        return func

    return _decorator


__all__ = [
    "NodeRegistry",
    "RegisteredNode",
    "NodeNotFoundError",
    "node",
]
