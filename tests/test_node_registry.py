"""Phase 0 — NodeRegistry + @node 装饰器测试.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §4.

铁律:
- 装饰器签名是 ``@node('factor.rev20', ...)`` 或 ``@node(type='factor.rev20', ...)``,
  type 是位置必填字符串。
- 装饰器不允许从 ``func.__name__`` 取 type, 这是 Python 路径与 workflow 解耦的硬约束。
- 重复 type 抛 ``ValueError``。
- ``@node()`` 不传 type → 签名层 ``TypeError`` (Python 强制).
- 不存在 type / unregister 后 ``get`` 抛 ``NodeNotFoundError`` (继承 ``WorkflowError``,
  不再继承 ``KeyError``).
- ``unregister`` 仍抛 plain ``KeyError`` (内部 ``del dict[key]`` 的语义).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from financial_analyst.workflow.errors import NodeNotFoundError, WorkflowError
from financial_analyst.workflow.registry import (
    NodeRegistry,
    RegisteredNode,
    node,
)


# 每条测试用独占 type 名, 测试后清掉, 防互相污染。
# 不用 _clear_registry_for_tests (生产代码也注册节点)。


@pytest.fixture
def isolated_types():
    """Track types registered in a test and unregister them on teardown."""
    registered: list[str] = []
    yield registered
    for t in registered:
        try:
            NodeRegistry.unregister(t)
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# 注册 & 取回
# ---------------------------------------------------------------------------


def test_node_decorator_registers_with_explicit_type(isolated_types: list[str]) -> None:
    t = "test_reg.foo_bar"
    isolated_types.append(t)

    @node(type=t)
    def compute(params: dict, inputs: dict) -> dict:
        return {"ok": True}

    got = NodeRegistry.get(t)
    assert isinstance(got, RegisteredNode)
    assert got.type == t
    assert got.compute is compute  # 装饰器返回原函数
    assert got.params_model is None
    assert got.outputs_model is None
    assert got.risk == "normal"
    assert got.pit is False
    assert got.meta == {}


def test_node_decorator_passes_meta_kwargs(isolated_types: list[str]) -> None:
    t = "test_reg.with_meta"
    isolated_types.append(t)

    class P(BaseModel):
        n: int = 1

    class O(BaseModel):
        rows: int

    # SP-W2A: ``tag`` 升级成显式 list[str] 字段, 不再走 **meta. 这里用一个
    # 未占用的关键字 ``owner`` 验 **meta 仍然透传到 ``meta`` dict.
    @node(type=t, params_model=P, outputs_model=O, risk="advice", pit=True, owner="alice", role="x")
    def compute(params: dict, inputs: dict) -> dict:
        return {"rows": 0}

    got = NodeRegistry.get(t)
    assert got.params_model is P
    assert got.outputs_model is O
    assert got.risk == "advice"
    assert got.pit is True
    assert got.meta == {"owner": "alice", "role": "x"}


# ---------------------------------------------------------------------------
# type 必须显式
# ---------------------------------------------------------------------------


def test_node_decorator_requires_explicit_type() -> None:
    """type=None: 签名是 ``str`` 但 Python runtime 不强制, 最终落到 register() 兜底 ValueError."""
    with pytest.raises(ValueError):

        @node(type=None)  # type: ignore[arg-type]
        def compute(params: dict, inputs: dict) -> dict:
            return {}


def test_node_decorator_rejects_empty_type() -> None:
    """空字符串也不行 — register() 兜底."""
    with pytest.raises(ValueError):

        @node(type="")
        def compute(params: dict, inputs: dict) -> dict:
            return {}


def test_node_decorator_requires_type_positionally_too() -> None:
    """``@node()`` 不传 type — 签名层 TypeError (位置必填参数缺失)."""
    with pytest.raises(TypeError):
        node()  # type: ignore[call-arg]


def test_node_decorator_accepts_type_positional(isolated_types: list[str]) -> None:
    """位置传 type 也能工作 (``@node('foo.bar')`` 形式)."""
    t = "test_reg.positional_type"
    isolated_types.append(t)

    @node(t)  # 位置参数, 不写 type=
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    assert NodeRegistry.get(t).compute is compute


def test_node_decorator_accepts_description(isolated_types: list[str]) -> None:
    """``description`` 落到 ``meta['description']``."""
    t = "test_reg.with_description"
    isolated_types.append(t)

    @node(type=t, description="reverse 20-day momentum")
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    got = NodeRegistry.get(t)
    assert got.meta == {"description": "reverse 20-day momentum"}


# ---------------------------------------------------------------------------
# 重复 type 抛 ValueError
# ---------------------------------------------------------------------------


def test_duplicate_type_raises_value_error(isolated_types: list[str]) -> None:
    t = "test_reg.duplicate"
    isolated_types.append(t)

    @node(type=t)
    def first(params: dict, inputs: dict) -> dict:
        return {}

    with pytest.raises(ValueError, match=t):

        @node(type=t)
        def second(params: dict, inputs: dict) -> dict:
            return {}


# ---------------------------------------------------------------------------
# get 缺失抛 NodeNotFoundError (继承 WorkflowError, 不再继承 KeyError);
# unregister 缺失仍抛 plain KeyError (内部 ``del dict[key]`` 的语义).
# ---------------------------------------------------------------------------


def test_get_missing_type_raises_node_not_found() -> None:
    with pytest.raises(NodeNotFoundError) as exc_info:
        NodeRegistry.get("not.exist.anywhere")
    # 异常树根: WorkflowError
    assert isinstance(exc_info.value, WorkflowError)
    # node_type 字段挂载 (便于上层结构化处理)
    assert exc_info.value.node_type == "not.exist.anywhere"


def test_get_missing_type_no_longer_subclass_of_key_error() -> None:
    """回归测试: NodeNotFoundError 已脱离 KeyError 谱系."""
    try:
        NodeRegistry.get("not.exist.anywhere")
    except NodeNotFoundError as e:
        assert not isinstance(e, KeyError), (
            "NodeNotFoundError 已迁到 errors.WorkflowError 谱系, 不再继承 KeyError"
        )
    else:
        pytest.fail("expected NodeNotFoundError")


def test_unregister_then_get_raises(isolated_types: list[str]) -> None:
    t = "test_reg.lifecycle"

    @node(type=t)
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    assert NodeRegistry.get(t).type == t
    NodeRegistry.unregister(t)
    with pytest.raises(NodeNotFoundError):
        NodeRegistry.get(t)


def test_unregister_missing_raises_key_error() -> None:
    """unregister 缺失仍抛 plain KeyError (内部 ``del cls._registry[type]`` 的语义)."""
    with pytest.raises(KeyError):
        NodeRegistry.unregister("never.registered.anywhere")


# ---------------------------------------------------------------------------
# list / all 接口
# ---------------------------------------------------------------------------


def test_registry_list_contains_registered_type(isolated_types: list[str]) -> None:
    t = "test_reg.listing"
    isolated_types.append(t)

    @node(type=t)
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    listed = NodeRegistry.list()
    assert t in listed
    assert listed[t].compute is compute


def test_registry_isolation_after_fixture_teardown(isolated_types: list[str]) -> None:
    """Sanity: 上一个测试 register 的 type 应已被 fixture 清掉.
    (跑一个会被清掉的 type, 然后这条测试单独验证 list 里没有早前测试的残留)。"""
    listed = NodeRegistry.list()
    # 前面用过的 type 名都应已被 teardown 清掉
    assert "test_reg.foo_bar" not in listed
    assert "test_reg.duplicate" not in listed
    assert "test_reg.lifecycle" not in listed
