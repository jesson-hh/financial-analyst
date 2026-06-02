"""Phase 0 — 端到端 workflow 测试.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §9.6.

跑通 3 节点 DAG (constant_universe → factor.zeros → eval.row_count), 校验:
- workflow JSON 落盘 (runner 自动写 ``workflow_runs/<run_id>/workflow.json``)
- 三个节点全部 SUCCESS 且顺序正确
- artifact_store 真落了文件 (universe → JSON ``{"codes":[...], "n":3}``,
  zeros → parquet 3 行 2 列, rowcount → JSON ``{"rows":3, "cols":2}``)
- run_log 6 行 (3 节点 × {RUNNING, SUCCESS}), 末态 SUCCESS

跟 ``test_workflow_runner.py`` 的区别: 那边节点是 case-local 临时 @node, 这边
import ``mock_nodes`` 让 ``@node`` 注册真正的 ``data.constant_universe`` /
``factor.zeros`` / ``eval.row_count`` 走一遍生产代码路径.

Realign 阶段对齐 spec §9.6: **不再用桥接器**, ``ArtifactStore`` 与 ``RunLog`` 都已
满足 ``ArtifactStoreProto`` / ``RunLogProto`` (4 参 write / 单 path 构造), runner 直接
吃, 测试也直接用真实 API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

# 触发 @node 注册到全局 NodeRegistry —— import 必须在 fixture / 测试函数引用
# NodeRegistry.get(...) 之前. 这里放模块顶层是有意为之: pytest 收集阶段就注册,
# 给同 session 内其它测试也能看到 (不冲突: type 名带 "data." / "factor." /
# "eval." 前缀, 与 test_workflow_runner.py 的 "test_runner." 前缀不撞).
from financial_analyst.workflow import mock_nodes  # noqa: F401  (side effect import)
from financial_analyst.workflow.artifacts import ArtifactStore
from financial_analyst.workflow.registry import NodeRegistry
from financial_analyst.workflow.run_log import RunLog
from financial_analyst.workflow.runner import WorkflowRunner
from financial_analyst.workflow.schema import NodeStatus


# ---------------------------------------------------------------------------
# Mock 节点注册的 sanity check (失败说明 mock_nodes import side-effect 没生效)
# ---------------------------------------------------------------------------


def test_mock_nodes_registered() -> None:
    """import mock_nodes 之后, 三个 type 在 NodeRegistry 里可查."""
    assert NodeRegistry.get("data.constant_universe").compute is mock_nodes.constant_universe
    assert NodeRegistry.get("factor.zeros").compute is mock_nodes.factor_zeros
    assert NodeRegistry.get("eval.row_count").compute is mock_nodes.row_count


# ---------------------------------------------------------------------------
# 端到端: 3 节点 workflow → runner → artifact + run_log 全验
# ---------------------------------------------------------------------------


def test_workflow_e2e_three_node_chain(tmp_path: Path) -> None:
    """3 节点 workflow 全跑通 + 全产物落盘 + run_log 完整 (spec §9.6).

    断言矩阵:
    1. RunResult.status == SUCCESS
    2. 3 条 SUCCESS 的 node_runs, 顺序 universe → zeros → rowcount
    3. workflow.json 在 ``<store.root>/workflow_runs/test_e2e/`` 落盘
    4. universe → JSON ``{"codes": [...], "n": 3}``
    5. zeros → parquet 3 行 2 列 (code + value)
    6. rowcount → JSON ``{"rows": 3, "cols": 2}``
    7. run_log.jsonl 6 行 (3 节点 × {RUNNING, SUCCESS}), latest_status 都 SUCCESS
    """
    codes = ["SH600519", "SZ000858", "SH601318"]

    # ---- 1. 构 3 节点 workflow dict (spec §9.6 形状)
    workflow = {
        "id": "wf_phase0_mock",
        "name": "Phase 0 mock 三节点 e2e",
        "version": 1,
        "nodes": [
            {
                "id": "universe",
                "type": "data.constant_universe",
                "params": {"codes": codes},
            },
            {
                "id": "zeros",
                "type": "factor.zeros",
                "inputs": {"universe": "universe.output"},
            },
            {
                "id": "rowcount",
                "type": "eval.row_count",
                "inputs": {"frame": "zeros.output"},
            },
        ],
    }

    # ---- 2. 装配 artifact store + runner (store.root + run_log_root 同 tmp)
    store_root = tmp_path / "artifacts_root"
    store = ArtifactStore(root=store_root)
    runner = WorkflowRunner(store=store, run_log_root=store_root)

    # ---- 3. 跑 runner
    result = runner.run(workflow, run_id="test_e2e")

    # ---- 4. RunResult 形状校验
    assert result.run_id == "test_e2e"
    assert result.workflow_id == "wf_phase0_mock"
    assert result.status == NodeStatus.SUCCESS, (
        f"整体 status 应 SUCCESS, 实际 {result.status} — node_runs="
        f"{[(nr.node_id, nr.status, nr.error) for nr in result.node_runs]}"
    )
    assert len(result.node_runs) == 3
    order = [nr.node_id for nr in result.node_runs]
    assert order == ["universe", "zeros", "rowcount"], (
        f"拓扑顺序错: expected universe→zeros→rowcount, got {order}"
    )
    for nr in result.node_runs:
        assert nr.status == NodeStatus.SUCCESS, (
            f"节点 {nr.node_id} status={nr.status} error={nr.error}"
        )

    # ---- 5. workflow.json 落盘 (runner 自动写)
    workflow_json_path = store_root / "workflow_runs" / "test_e2e" / "workflow.json"
    assert workflow_json_path.exists(), "runner 没写 workflow.json"
    reloaded = json.loads(workflow_json_path.read_text(encoding="utf-8"))
    assert reloaded["id"] == "wf_phase0_mock"
    assert len(reloaded["nodes"]) == 3

    # ---- 6. artifact 文件物理存在
    assert store.exists("test_e2e", "universe"), "universe artifact 没落盘"
    assert store.exists("test_e2e", "zeros"), "zeros artifact 没落盘"
    assert store.exists("test_e2e", "rowcount"), "rowcount artifact 没落盘"

    # universe → JSON ({"codes": [...], "n": 3})
    universe_payload = store.read("test_e2e", "universe")
    assert isinstance(universe_payload, dict)
    assert universe_payload == {"codes": codes, "n": len(codes)}
    universe_files = list(
        (store_root / "workflow_runs" / "test_e2e" / "nodes" / "universe").iterdir()
    )
    assert any(p.suffix == ".json" for p in universe_files), (
        f"universe 输出应是 JSON, 实际文件: {[p.name for p in universe_files]}"
    )

    # zeros → parquet (DataFrame 3 行 2 列: code + value)
    zeros_files = list(
        (store_root / "workflow_runs" / "test_e2e" / "nodes" / "zeros").iterdir()
    )
    assert any(p.suffix == ".parquet" for p in zeros_files), (
        f"zeros 输出应是 parquet, 实际文件: {[p.name for p in zeros_files]}"
    )
    zeros_df = store.read("test_e2e", "zeros")
    assert isinstance(zeros_df, pd.DataFrame)
    assert zeros_df.shape == (3, 2)
    assert set(zeros_df.columns) == {"code", "value"}
    assert zeros_df["code"].tolist() == codes
    assert zeros_df["value"].tolist() == [0.0, 0.0, 0.0]

    # rowcount → JSON ({"rows": 3, "cols": 2})
    rowcount_files = list(
        (store_root / "workflow_runs" / "test_e2e" / "nodes" / "rowcount").iterdir()
    )
    assert any(p.suffix == ".json" for p in rowcount_files), (
        f"rowcount 输出应是 JSON, 实际文件: {[p.name for p in rowcount_files]}"
    )
    rowcount_payload = store.read("test_e2e", "rowcount")
    assert rowcount_payload == {"rows": 3, "cols": 2}

    # ---- 7. run_log 6 条 (3 节点 × {RUNNING, SUCCESS})
    run_log = RunLog(store_root / "workflow_runs" / "test_e2e" / "run_log.jsonl")
    log_entries = run_log.read_all()
    assert len(log_entries) == 6, (
        f"run_log 应 6 条 (3 节点 × {{RUNNING, SUCCESS}}), 实际 {len(log_entries)}: "
        f"{[(e.node_id, e.status) for e in log_entries]}"
    )

    success_entries = [e for e in log_entries if e.status == NodeStatus.SUCCESS]
    assert len(success_entries) == 3
    success_node_ids = {e.node_id for e in success_entries}
    assert success_node_ids == {"universe", "zeros", "rowcount"}

    running_entries = [e for e in log_entries if e.status == NodeStatus.RUNNING]
    assert len(running_entries) == 3
    assert {e.node_id for e in running_entries} == {"universe", "zeros", "rowcount"}

    # ---- 8. latest_status — 末态全 SUCCESS
    assert run_log.latest_status("universe") == NodeStatus.SUCCESS
    assert run_log.latest_status("zeros") == NodeStatus.SUCCESS
    assert run_log.latest_status("rowcount") == NodeStatus.SUCCESS

    # ---- 9. run_log.jsonl 物理文件, 每行可独立解析
    assert run_log.path.exists()
    raw_lines = run_log.path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 6
    for line in raw_lines:
        payload = json.loads(line)
        # Enum 落字符串
        assert payload["status"] in ("running", "success"), (
            f"非法 status: {payload['status']}"
        )

    # ---- 10. RunResult.artifacts_root 是真实物理路径 (不是 mem://)
    assert "mem://" not in result.artifacts_root
    assert "workflow_runs/test_e2e" in result.artifacts_root


# ---------------------------------------------------------------------------
# 反 case: workflow 同模板, codes 改成 7 个, 仍然全跑通 → rows=7, cols=2
# (轻量交叉验证, 防 row_count 把 codes 长度算死成 3)
# ---------------------------------------------------------------------------


def test_workflow_e2e_different_universe_size(tmp_path: Path) -> None:
    """同一 workflow 模板, 换 codes 长度, rows 应跟着变."""
    codes = ["A", "B", "C", "D", "E", "F", "G"]
    workflow = {
        "id": "wf_phase0_mock_v2",
        "name": "Phase 0 mock 三节点 e2e (n=7)",
        "version": 1,
        "nodes": [
            {
                "id": "universe",
                "type": "data.constant_universe",
                "params": {"codes": codes},
            },
            {
                "id": "zeros",
                "type": "factor.zeros",
                "inputs": {"universe": "universe.output"},
            },
            {
                "id": "rowcount",
                "type": "eval.row_count",
                "inputs": {"frame": "zeros.output"},
            },
        ],
    }

    store = ArtifactStore(root=tmp_path / "artifacts")
    runner = WorkflowRunner(store=store, run_log_root=tmp_path / "artifacts")
    result = runner.run(workflow, run_id="test_e2e_v2")

    assert result.status == NodeStatus.SUCCESS
    assert store.read("test_e2e_v2", "rowcount") == {"rows": 7, "cols": 2}
    # universe payload 也跟着变
    assert store.read("test_e2e_v2", "universe") == {"codes": codes, "n": 7}
