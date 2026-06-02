"""SP-W2A — NodeRegistry list_by_group / list_by_tag 测试.

新加 ``group`` / ``tag`` 字段 + 两个过滤接口. 验:
1. @node(group=..., tag=[...]) 字段持久化到 RegisteredNode
2. list_by_group(g) 精确匹配 group
3. list_by_tag(t) 命中 tag list 内任一
4. 默认 group='misc', tag=[]
5. 向后兼容: 不传 group/tag 也工作
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from financial_analyst.workflow.registry import (
    NodeRegistry,
    RegisteredNode,
    node,
)


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
# group / tag 字段持久化
# ---------------------------------------------------------------------------


def test_node_decorator_persists_group_and_tag(isolated_types: list[str]) -> None:
    t = "test_grouping.with_group_tag"
    isolated_types.append(t)

    @node(type=t, group="data", tag=["data", "loader"])
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    got = NodeRegistry.get(t)
    assert got.group == "data"
    assert got.tag == ["data", "loader"]


def test_node_decorator_defaults_group_misc_and_tag_empty(isolated_types: list[str]) -> None:
    t = "test_grouping.defaults"
    isolated_types.append(t)

    @node(type=t)
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    got = NodeRegistry.get(t)
    assert got.group == "misc"
    assert got.tag == []


def test_registered_node_dataclass_has_group_and_tag_fields() -> None:
    """RegisteredNode dataclass 自身 schema 必须含两个新字段."""
    fields = {f.name for f in RegisteredNode.__dataclass_fields__.values()}
    assert "group" in fields
    assert "tag" in fields


def test_node_decorator_accepts_tag_none_as_empty(isolated_types: list[str]) -> None:
    """``tag=None`` 应等价于 ``tag=[]`` (实现选择, 防可变默认参数坑)."""
    t = "test_grouping.tag_none"
    isolated_types.append(t)

    @node(type=t, tag=None)
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    assert NodeRegistry.get(t).tag == []


# ---------------------------------------------------------------------------
# list_by_group
# ---------------------------------------------------------------------------


def test_list_by_group_filters_exact_match(isolated_types: list[str]) -> None:
    t1 = "test_grouping.lbg_a"
    t2 = "test_grouping.lbg_b"
    t3 = "test_grouping.lbg_c"
    isolated_types.extend([t1, t2, t3])

    @node(type=t1, group="data")
    def n1(params: dict, inputs: dict) -> dict:
        return {}

    @node(type=t2, group="data")
    def n2(params: dict, inputs: dict) -> dict:
        return {}

    @node(type=t3, group="factor")
    def n3(params: dict, inputs: dict) -> dict:
        return {}

    data_nodes = NodeRegistry.list_by_group("data")
    types = {n.type for n in data_nodes}
    assert t1 in types
    assert t2 in types
    assert t3 not in types


def test_list_by_group_returns_empty_for_unknown_group() -> None:
    """没有任何节点挂这个 group → 返 []."""
    out = NodeRegistry.list_by_group("totally_unused_group_xyz_123")
    assert out == []


def test_list_by_group_sorted_by_type(isolated_types: list[str]) -> None:
    t_z = "test_grouping.lbg_zzz"
    t_a = "test_grouping.lbg_aaa"
    isolated_types.extend([t_z, t_a])

    @node(type=t_z, group="sort_test")
    def n_z(params: dict, inputs: dict) -> dict:
        return {}

    @node(type=t_a, group="sort_test")
    def n_a(params: dict, inputs: dict) -> dict:
        return {}

    out = NodeRegistry.list_by_group("sort_test")
    assert [n.type for n in out] == [t_a, t_z]


# ---------------------------------------------------------------------------
# list_by_tag
# ---------------------------------------------------------------------------


def test_list_by_tag_matches_any_in_list(isolated_types: list[str]) -> None:
    t1 = "test_grouping.lbt_a"
    t2 = "test_grouping.lbt_b"
    t3 = "test_grouping.lbt_c"
    isolated_types.extend([t1, t2, t3])

    @node(type=t1, tag=["factor", "backtest"])
    def n1(params: dict, inputs: dict) -> dict:
        return {}

    @node(type=t2, tag=["factor"])
    def n2(params: dict, inputs: dict) -> dict:
        return {}

    @node(type=t3, tag=["data"])
    def n3(params: dict, inputs: dict) -> dict:
        return {}

    factor_nodes = NodeRegistry.list_by_tag("factor")
    types = {n.type for n in factor_nodes}
    assert t1 in types
    assert t2 in types
    assert t3 not in types

    backtest_nodes = NodeRegistry.list_by_tag("backtest")
    bt_types = {n.type for n in backtest_nodes}
    assert t1 in bt_types
    assert t2 not in bt_types  # tag=['factor'] no backtest


def test_list_by_tag_returns_empty_for_unknown_tag() -> None:
    out = NodeRegistry.list_by_tag("totally_unused_tag_xyz_123")
    assert out == []


def test_list_by_tag_sorted_by_type(isolated_types: list[str]) -> None:
    t_z = "test_grouping.lbt_zzz"
    t_a = "test_grouping.lbt_aaa"
    isolated_types.extend([t_z, t_a])

    @node(type=t_z, tag=["sort_tag_test"])
    def n_z(params: dict, inputs: dict) -> dict:
        return {}

    @node(type=t_a, tag=["sort_tag_test"])
    def n_a(params: dict, inputs: dict) -> dict:
        return {}

    out = NodeRegistry.list_by_tag("sort_tag_test")
    assert [n.type for n in out] == [t_a, t_z]


# ---------------------------------------------------------------------------
# 与 params_model + 其它字段共存
# ---------------------------------------------------------------------------


def test_group_tag_coexist_with_all_other_kwargs(isolated_types: list[str]) -> None:
    t = "test_grouping.full_kwargs"
    isolated_types.append(t)

    class P(BaseModel):
        x: int = 1

    @node(
        type=t,
        params_model=P,
        risk="advice",
        pit=True,
        group="risk",
        tag=["alpha", "risk"],
        description="full kwargs sanity",
        owner="alice",
    )
    def compute(params: dict, inputs: dict) -> dict:
        return {}

    got = NodeRegistry.get(t)
    assert got.params_model is P
    assert got.risk == "advice"
    assert got.pit is True
    assert got.group == "risk"
    assert got.tag == ["alpha", "risk"]
    assert got.meta.get("description") == "full kwargs sanity"
    assert got.meta.get("owner") == "alice"
