"""Phase 0 — WorkflowRunner 行为测试.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §5.
Realign 阶段对齐:
- ``WorkflowRunner(store, run_log_root)`` 必填 — 不再有内置 InMemory 兜底.
- ``run(workflow_or_dict, run_id=...)`` 入参收窄到 Workflow / dict.
- 环检测抛 ``CycleError(cycle_nodes=[...])``, 不再 ``ValueError('cycle detected')``.
- ``outputs_model`` 严格 (修 H3): 设置了 outputs_model 但返回非 dict → FAILED.

覆盖矩阵 (任务硬要求):
1. 单节点 workflow 成功 → RunResult.status == SUCCESS.
2. 3 节点链 (A→B→C) 全部成功 + 拓扑顺序正确.
3. 中间节点失败 → 该节点 FAILED, 下游 SKIPPED, 上游 SUCCESS, **整体不抛**.
4. 含环 workflow → run() 抛 ``CycleError`` (含 cycle_nodes 字段).
5. 节点 type 未在 NodeRegistry → run() 立即拒 (ValueError, 包了 NodeNotFoundError).
6. 节点失败时 error.txt 落盘 (含 traceback).
7. outputs_model 设置但返回非 dict → FAILED (修 H3).
8. ``__init__`` 不允许 store/run_log_root 为 None.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from financial_analyst.workflow.artifacts import ArtifactStore
from financial_analyst.workflow.errors import CycleError, NodeNotFoundError
from financial_analyst.workflow.registry import NodeRegistry, node
from financial_analyst.workflow.runner import WorkflowRunner
from financial_analyst.workflow.schema import NodeStatus


# ---------------------------------------------------------------------------
# 测试隔离: 每条 case 用独占 type 名, teardown 时 unregister.
# 不用 _clear_registry_for_tests (生产代码也会注册节点).
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_types():
    """Track registered types and unregister on teardown."""
    registered: list[str] = []
    yield registered
    for t in registered:
        try:
            NodeRegistry.unregister(t)
        except KeyError:
            pass


@pytest.fixture
def runner(tmp_path: Path) -> WorkflowRunner:
    """通用 fixture: 落 tmp_path/store 的 ArtifactStore + 同根 run_log_root."""
    store_root = tmp_path / "store"
    store = ArtifactStore(root=store_root)
    return WorkflowRunner(store=store, run_log_root=store_root)


# ---------------------------------------------------------------------------
# 0. 构造校验
# ---------------------------------------------------------------------------


def test_runner_requires_store(tmp_path: Path) -> None:
    """store=None 立即拒 — 不允许 silent fallback."""
    with pytest.raises(ValueError, match="store"):
        WorkflowRunner(store=None, run_log_root=tmp_path)  # type: ignore[arg-type]


def test_runner_requires_run_log_root(tmp_path: Path) -> None:
    """run_log_root=None 立即拒."""
    store = ArtifactStore(root=tmp_path / "s")
    with pytest.raises(ValueError, match="run_log_root"):
        WorkflowRunner(store=store, run_log_root=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Case 1: 单节点 workflow 成功
# ---------------------------------------------------------------------------


def test_single_node_success(
    isolated_types: list[str], runner: WorkflowRunner
) -> None:
    """单节点 workflow 跑通, RunResult.status = SUCCESS, 节点 SUCCESS."""
    t = "test_runner.single_ok"
    isolated_types.append(t)

    @node(type=t)
    def compute(params: dict, inputs: dict) -> dict:
        return {"hello": "world", "n": params.get("n", 0)}

    workflow = {
        "id": "wf_single",
        "name": "single node ok",
        "nodes": [{"id": "only", "type": t, "params": {"n": 42}}],
    }

    result = runner.run(workflow, run_id="r_single")

    assert result.run_id == "r_single"
    assert result.workflow_id == "wf_single"
    assert result.status == NodeStatus.SUCCESS
    assert len(result.node_runs) == 1
    only = result.node_runs[0]
    assert only.node_id == "only"
    assert only.node_type == t
    assert only.status == NodeStatus.SUCCESS
    assert only.input_hash is not None and len(only.input_hash) == 16
    assert only.output_artifact_uri is not None
    # artifact URI 是 POSIX 相对路径字符串
    assert only.output_artifact_uri.endswith(".json")
    assert "\\" not in only.output_artifact_uri
    assert only.error is None
    assert result.duration_ms is not None and result.duration_ms >= 0
    # artifacts_root 是真实物理路径, 不是 mem://
    assert "mem://" not in result.artifacts_root
    assert "workflow_runs/r_single" in result.artifacts_root


# ---------------------------------------------------------------------------
# Case 2: 3 节点链 A→B→C, 拓扑顺序 + 数据流
# ---------------------------------------------------------------------------


def test_three_node_chain_topo_order(
    isolated_types: list[str], runner: WorkflowRunner
) -> None:
    """A produces 'value=10', B doubles to 20, C reads 20 and returns 'final'."""

    t_a = "test_runner.chain_a"
    t_b = "test_runner.chain_b"
    t_c = "test_runner.chain_c"
    isolated_types.extend([t_a, t_b, t_c])

    @node(type=t_a)
    def step_a(params: dict, inputs: dict) -> dict:
        return {"value": 10}

    @node(type=t_b)
    def step_b(params: dict, inputs: dict) -> dict:
        upstream = inputs["src"]
        return {"value": upstream["value"] * 2}

    @node(type=t_c)
    def step_c(params: dict, inputs: dict) -> dict:
        upstream = inputs["src"]
        return {"final": upstream["value"], "tag": "done"}

    workflow = {
        "id": "wf_chain",
        "name": "A->B->C",
        "nodes": [
            {"id": "C", "type": t_c, "inputs": {"src": "B.output"}},
            {"id": "A", "type": t_a},
            {"id": "B", "type": t_b, "inputs": {"src": "A.output"}},
        ],
    }

    result = runner.run(workflow, run_id="r_chain")

    assert result.status == NodeStatus.SUCCESS
    assert len(result.node_runs) == 3

    # 拓扑顺序: A → B → C, 不管 workflow.nodes 列表序如何
    order = [nr.node_id for nr in result.node_runs]
    assert order == ["A", "B", "C"], f"expected A→B→C, got {order}"
    # 全部 SUCCESS
    assert all(nr.status == NodeStatus.SUCCESS for nr in result.node_runs)


# ---------------------------------------------------------------------------
# Case 3: 中间节点失败 → 下游 SKIPPED, 上游 SUCCESS, 整体 FAILED, 不抛
# ---------------------------------------------------------------------------


def test_mid_node_failure_skips_downstream(
    isolated_types: list[str], runner: WorkflowRunner, tmp_path: Path
) -> None:
    """A SUCCESS, B 抛 RuntimeError, C 依赖 B 应 SKIPPED. 整体 FAILED. run() 不抛.

    顺手验 error.txt 落盘 (修 spec §5.2.g).
    """

    t_a = "test_runner.fail_a"
    t_b = "test_runner.fail_b"
    t_c = "test_runner.fail_c"
    isolated_types.extend([t_a, t_b, t_c])

    @node(type=t_a)
    def step_a(params: dict, inputs: dict) -> dict:
        return {"value": 1}

    @node(type=t_b)
    def step_b(params: dict, inputs: dict) -> dict:
        raise RuntimeError("boom from B")

    @node(type=t_c)
    def step_c(params: dict, inputs: dict) -> dict:
        return {"unreachable": True}

    workflow = {
        "id": "wf_fail",
        "name": "A->B(fail)->C",
        "nodes": [
            {"id": "A", "type": t_a},
            {"id": "B", "type": t_b, "inputs": {"src": "A.output"}},
            {"id": "C", "type": t_c, "inputs": {"src": "B.output"}},
        ],
    }

    # 关键: run() 不抛, 失败作为结果返回
    result = runner.run(workflow, run_id="r_fail")

    assert result.status == NodeStatus.FAILED, "任一节点 FAILED → 整体 FAILED"

    by_id = {nr.node_id: nr for nr in result.node_runs}
    assert by_id["A"].status == NodeStatus.SUCCESS
    assert by_id["B"].status == NodeStatus.FAILED
    assert by_id["B"].error is not None
    assert "boom from B" in by_id["B"].error
    assert "RuntimeError" in by_id["B"].error
    assert by_id["C"].status == NodeStatus.SKIPPED

    # error.txt 落盘 (修 spec §5.2.g)
    error_txt = (
        tmp_path / "store" / "workflow_runs" / "r_fail" / "nodes" / "B" / "error.txt"
    )
    assert error_txt.exists(), "节点失败应写 error.txt"
    err_content = error_txt.read_text(encoding="utf-8")
    assert "boom from B" in err_content
    assert "RuntimeError" in err_content
    assert "traceback" in err_content.lower()


def test_independent_branch_keeps_running_after_failure(
    isolated_types: list[str], runner: WorkflowRunner
) -> None:
    """两条独立分支 A→B(fail), X→Y(ok). X/Y 不依赖失败分支, 应跑成功."""
    t_a = "test_runner.indep_a"
    t_b = "test_runner.indep_b"
    t_x = "test_runner.indep_x"
    t_y = "test_runner.indep_y"
    isolated_types.extend([t_a, t_b, t_x, t_y])

    @node(type=t_a)
    def a(params: dict, inputs: dict) -> dict:
        return {"v": 1}

    @node(type=t_b)
    def b(params: dict, inputs: dict) -> dict:
        raise ValueError("B failed")

    @node(type=t_x)
    def x(params: dict, inputs: dict) -> dict:
        return {"v": 100}

    @node(type=t_y)
    def y(params: dict, inputs: dict) -> dict:
        return {"v": inputs["src"]["v"] + 1}

    workflow = {
        "id": "wf_branches",
        "name": "two independent branches",
        "nodes": [
            {"id": "A", "type": t_a},
            {"id": "B", "type": t_b, "inputs": {"src": "A.output"}},
            {"id": "X", "type": t_x},
            {"id": "Y", "type": t_y, "inputs": {"src": "X.output"}},
        ],
    }
    result = runner.run(workflow, run_id="r_branches")

    by_id = {nr.node_id: nr for nr in result.node_runs}
    assert by_id["A"].status == NodeStatus.SUCCESS
    assert by_id["B"].status == NodeStatus.FAILED
    # 独立分支不受牵连
    assert by_id["X"].status == NodeStatus.SUCCESS
    assert by_id["Y"].status == NodeStatus.SUCCESS
    assert result.status == NodeStatus.FAILED  # 整体仍 FAILED (有 B 失败)


# ---------------------------------------------------------------------------
# Case 4: 环检测 — A→B 且 B→A → CycleError
# ---------------------------------------------------------------------------


def test_cycle_detected_raises(
    isolated_types: list[str], runner: WorkflowRunner
) -> None:
    """A→B + B→A 形成环, run() 抛 ``CycleError(cycle_nodes=[...])``."""

    t_a = "test_runner.cycle_a"
    t_b = "test_runner.cycle_b"
    isolated_types.extend([t_a, t_b])

    @node(type=t_a)
    def a(params: dict, inputs: dict) -> dict:
        return {"v": 1}

    @node(type=t_b)
    def b(params: dict, inputs: dict) -> dict:
        return {"v": 2}

    workflow = {
        "id": "wf_cycle",
        "name": "A<->B cycle",
        "nodes": [
            {"id": "A", "type": t_a, "inputs": {"src": "B.output"}},
            {"id": "B", "type": t_b, "inputs": {"src": "A.output"}},
        ],
    }

    with pytest.raises(CycleError) as exc_info:
        runner.run(workflow, run_id="r_cycle")

    # CycleError 携带 cycle_nodes 字段
    assert exc_info.value.cycle_nodes == ["A", "B"]


# ---------------------------------------------------------------------------
# Case 5: 缺失 type → 立即拒
# ---------------------------------------------------------------------------


def test_missing_node_type_raises_immediately(runner: WorkflowRunner) -> None:
    """workflow 引用未注册的 type, runner 立即抛 ValueError (不进入执行循环).

    这跟节点级失败 (RuntimeError 在 compute 内) 不一样: 形状不对的 workflow
    根本不能跑, 跟 cycle 同等级.

    runner 把 NodeNotFoundError 包成 ValueError + __cause__ 链, 老代码 ``except
    ValueError`` 仍接得住, 结构化捕获走 ``__cause__``.
    """
    workflow = {
        "id": "wf_missing",
        "name": "ref unregistered type",
        "nodes": [
            {"id": "ghost", "type": "test_runner.never_registered_xyz"},
        ],
    }

    with pytest.raises(ValueError, match="never_registered_xyz") as exc_info:
        runner.run(workflow, run_id="r_missing")

    # __cause__ 链到 NodeNotFoundError (结构化捕获)
    assert isinstance(exc_info.value.__cause__, NodeNotFoundError)
    assert exc_info.value.__cause__.node_type == "test_runner.never_registered_xyz"


# ---------------------------------------------------------------------------
# Case 6: outputs_model 严格 (修 H3) — 设置 model 但返回非 dict → FAILED
# ---------------------------------------------------------------------------


def test_outputs_model_non_dict_return_fails(
    isolated_types: list[str], runner: WorkflowRunner
) -> None:
    """节点挂了 outputs_model 却返回 DataFrame/Series/标量 → FAILED (NodeExecutionError).

    H3 修复点: 之前 ``_validate_outputs`` 对非 dict 宽松放行, outputs_model 形同摆设.
    现在 outputs_model 设置就硬性要求 dict.
    """
    t = "test_runner.bad_outputs"
    isolated_types.append(t)

    class O(BaseModel):
        rows: int

    @node(type=t, outputs_model=O)
    def compute(params: dict, inputs: dict) -> list:
        # 故意返 list (非 dict) 让 _validate_outputs 拒
        return [1, 2, 3]

    workflow = {
        "id": "wf_bad_outputs",
        "name": "bad output shape",
        "nodes": [{"id": "only", "type": t}],
    }

    result = runner.run(workflow, run_id="r_bad_outputs")

    assert result.status == NodeStatus.FAILED
    only = result.node_runs[0]
    assert only.status == NodeStatus.FAILED
    assert only.error is not None
    assert "outputs_model" in only.error.lower() or "non-dict" in only.error.lower() or "NodeExecutionError" in only.error


def test_outputs_model_validation_failure(
    isolated_types: list[str], runner: WorkflowRunner
) -> None:
    """outputs_model 设了, 返回 dict 但形状错 → FAILED (Pydantic ValidationError)."""
    t = "test_runner.bad_shape"
    isolated_types.append(t)

    class O(BaseModel):
        rows: int

    @node(type=t, outputs_model=O)
    def compute(params: dict, inputs: dict) -> dict:
        return {"oops": "wrong field"}

    workflow = {
        "id": "wf_bad_shape",
        "name": "bad output shape",
        "nodes": [{"id": "only", "type": t}],
    }

    result = runner.run(workflow, run_id="r_bad_shape")

    assert result.status == NodeStatus.FAILED
    only = result.node_runs[0]
    assert only.status == NodeStatus.FAILED
    assert only.error is not None
    # Pydantic ValidationError 信息含 rows / missing 或 ValidationError 类名
    assert "ValidationError" in only.error or "rows" in only.error
