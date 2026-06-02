"""WorkflowRunner — Phase 0 同步执行器.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §5.
Realign 阶段对齐契约 (workflow/protocols.py):

- ``__init__(store: ArtifactStoreProto, run_log_root: Path)``: store 与 run_log_root 必填.
  **删除** 内置 ``_InMemoryArtifactStore`` / ``_InMemoryRunLog`` 整段 — 运行 = 落盘, 不
  再有内存兜底 (Phase 0 spec §5 + §6.3 都要求物理目录, 内存版让契约漂移).
- ``run(workflow)`` 入参收窄到 ``Workflow | dict``, **不再**支持 ``str`` / ``Path`` (与
  spec §5.1 一致, 文件加载留给调用方; 也避免 ``str`` 路径 vs JSON 字符串歧义).
- ``run`` 起始 mkdir ``run_log_root/workflow_runs/<run_id>/nodes/``, 同目录 dump
  ``workflow.json`` (用户传 dict 也能复原).
- ``artifacts_root`` 用 ``store.root`` + ``"workflow_runs/<run_id>"`` 拼真实物理路径,
  **不写死 ``mem://``**.
- 节点失败把 ``traceback.format_exc()`` 写 ``nodes/<node_id>/error.txt`` (spec §5.2.g).
- ``CycleError`` 替换原 ``ValueError("cycle detected: ...")``, ``cycle_nodes`` 字段挂残余.
- 节点级 ``except (NodeNotFoundError, ValidationError, NodeExecutionError, Exception)``
  收紧 — ``(OSError, MemoryError, KeyboardInterrupt, SystemExit)`` 让冒泡 (整 run abort,
  不当节点失败处理). H2: input_hash 删 ``default=str``, params 必须 JSON-native
  (Pydantic 已强制). H3: ``outputs_model != None`` 必校验输出是 dict 后再 ``model_validate``,
  非 dict 抛 ``NodeExecutionError``.

执行步骤 (固定顺序):
1. 加载 (Workflow / dict)
2. Pydantic 校验 + 显式 topo 排序 + 查环 (抛 ``CycleError``)
3. mkdir run_dir + 写 workflow.json
4. 按拓扑顺序串行执行每个节点:
   a. 从上游 artifact 读 inputs (store.read 走 ``(run_id, up_id, "output")``)
   b. 计算 input hash (json.dumps sort_keys=True + sha256[:16])
   c. registry.get(type) 取 RegisteredNode + Pydantic 验 params
   d. 调 compute, try/except 包住; 失败写 error.txt + NodeRun(FAILED)
   e. 成功 → outputs_model 校验 + write artifact + append run_log
   f. 上游 FAILED/SKIPPED 时本节点 SKIPPED, **整个 run 不抛全局**
5. 汇总 RunResult
"""

from __future__ import annotations

import hashlib
import json
import time
import traceback
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from financial_analyst.workflow.errors import (
    CycleError,
    NodeExecutionError,
    NodeNotFoundError,
)
from financial_analyst.workflow.protocols import (
    ArtifactStoreProto,
    Payload,
    RunLogProto,
)
from financial_analyst.workflow.registry import NodeRegistry, RegisteredNode
from financial_analyst.workflow.run_log import RunLog
from financial_analyst.workflow.schema import (
    Node,
    NodeRun,
    NodeStatus,
    RunResult,
    Workflow,
)

# 让 NodeNotFoundError 仍在 runner 命名空间可见 (老代码 ``from runner import
# NodeNotFoundError`` 不破)
__all_re_exports__ = (NodeNotFoundError,)


# ---------------------------------------------------------------------------
# Workflow 加载 / topo / 工具
# ---------------------------------------------------------------------------


def _coerce_workflow(workflow: Workflow | dict) -> Workflow:
    """统一两种入参形态 -> Workflow.

    收窄: 不再支持 ``str`` / ``Path``. 文件加载由调用方完成 (Phase 0 spec §5.1 +
    避免 "str 是路径还是 JSON" 歧义).
    """
    if isinstance(workflow, Workflow):
        return workflow
    if isinstance(workflow, dict):
        return Workflow.model_validate(workflow)
    raise TypeError(
        f"workflow 入参必须是 Workflow / dict, 不接受 {type(workflow).__name__} — "
        "文件路径请调用方先 json.loads(path.read_text())."
    )


def _parse_endpoint(endpoint: str) -> tuple[str, str]:
    """``"node_id.port"`` -> ``(node_id, port)``. 缺 ``.`` 抛 ValueError."""
    if "." not in endpoint:
        raise ValueError(
            f"端点 {endpoint!r} 形状错误, 必须是 '<node_id>.<port>'"
        )
    node_id, _, port = endpoint.partition(".")
    if not node_id or not port:
        raise ValueError(
            f"端点 {endpoint!r} 节点 id 或端口名为空"
        )
    return node_id, port


def _build_dag(workflow: Workflow) -> dict[str, set[str]]:
    """合并 ``node.inputs`` 和 ``workflow.edges``, 返回邻接表
    ``{node_id: set(upstream_node_id)}``.

    - 校验所有引用的 node_id 在 workflow 内
    - 校验端点形如 ``"<node_id>.<port>"``
    """
    node_ids = {n.id for n in workflow.nodes}
    upstream: dict[str, set[str]] = {n.id: set() for n in workflow.nodes}

    # node.inputs
    for n in workflow.nodes:
        for _input_name, ref in n.inputs.items():
            up_id, _ = _parse_endpoint(ref)
            if up_id not in node_ids:
                raise ValueError(
                    f"Node {n.id!r} inputs 引用了不存在的节点 {up_id!r}"
                )
            upstream[n.id].add(up_id)

    # workflow.edges
    for e in workflow.edges:
        up_id, _ = _parse_endpoint(e.from_)
        down_id, _ = _parse_endpoint(e.to)
        if up_id not in node_ids:
            raise ValueError(
                f"Edge from {e.from_!r} 引用了不存在的节点 {up_id!r}"
            )
        if down_id not in node_ids:
            raise ValueError(
                f"Edge to {e.to!r} 引用了不存在的节点 {down_id!r}"
            )
        upstream[down_id].add(up_id)

    return upstream


def _topo_sort(upstream: dict[str, set[str]]) -> list[str]:
    """Kahn 拓扑排序. 残余节点 > 0 抛 ``CycleError(cycle_nodes=[...])``."""
    # 入度 = upstream 集合大小
    in_degree: dict[str, int] = {nid: len(ups) for nid, ups in upstream.items()}
    # 反向邻接: downstream[u] = 依赖 u 的节点集合
    downstream: dict[str, set[str]] = defaultdict(set)
    for nid, ups in upstream.items():
        for u in ups:
            downstream[u].add(nid)

    queue: deque[str] = deque(
        sorted(nid for nid, deg in in_degree.items() if deg == 0)
    )
    order: list[str] = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        # 排序后入队保证确定性 (同层节点按 id 字典序)
        for down in sorted(downstream[nid]):
            in_degree[down] -= 1
            if in_degree[down] == 0:
                queue.append(down)

    if len(order) != len(upstream):
        residual = sorted(set(upstream) - set(order))
        raise CycleError(cycle_nodes=residual)
    return order


def _iso_utc_now() -> str:
    """ISO 8601 UTC 时间字符串 (带 Z 后缀)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _compute_input_hash(
    params: dict[str, Any], input_uris: dict[str, str]
) -> str:
    """sha256(json.dumps({params, inputs_uris}, sort_keys=True))[:16].

    spec §5.2.c: inputs 取 URI 不取内容 (大 DataFrame 不能塞 hash 里).

    修 H2: ``default=str`` 已删除 — params 经过 Pydantic ``model_validate(...)
    .model_dump()`` 后应该全是 JSON-native 类型. 若节点 schema 漏了不可序列化字段
    (datetime / set 等), 这里直接抛 ``TypeError``, 让 schema 作者修而非用 ``str()``
    把任意对象当字符串当 hash 输入 (那样 hash 不稳定, 同一对象不同 repr 哈希不同).
    """
    payload = {"params": params, "inputs_uris": input_uris}
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _validate_params(
    params: dict[str, Any], params_model: type[BaseModel] | None
) -> dict[str, Any]:
    """若 params_model 非 None, 用 it.model_validate(params).model_dump() 走一遍.

    校验失败让 ValidationError 冒泡 (调用方 try/except 包成 FAILED).
    """
    if params_model is None:
        return params
    validated = params_model.model_validate(params)
    return validated.model_dump()


def _validate_outputs(
    outputs: Any, outputs_model: type[BaseModel] | None
) -> Any:
    """若 outputs_model 非 None, 强制 ``isinstance(outputs, dict)`` 后再 ``model_validate``.

    修 H3: outputs_model 已显式声明输出形状, 节点却返回非 dict (DataFrame / Series /
    标量) 是契约冲突 — 之前的宽松放行让 outputs_model 形同摆设. 现在统一硬拒.
    DataFrame 输出场景节点应**不挂** outputs_model (mock_nodes.factor_zeros 就是这样).

    Returns
    -------
    Any
        outputs_model is None → 原样返回
        outputs_model 校验通过 → ``model_validate(outputs).model_dump()``

    Raises
    ------
    NodeExecutionError
        outputs_model 设置但 outputs 不是 dict.
    ValidationError
        outputs_model 设置, outputs 是 dict 但形状不对 (Pydantic 校验失败).
    """
    if outputs_model is None:
        return outputs
    if not isinstance(outputs, dict):
        raise NodeExecutionError(
            node_id="<unknown>",  # caller 包一层时填实际 node_id
            original=TypeError(
                f"outputs_model={outputs_model.__name__} 设置但 compute 返回非 dict "
                f"({type(outputs).__name__}); DataFrame/Series 输出请不要挂 outputs_model."
            ),
        )
    validated = outputs_model.model_validate(outputs)
    return validated.model_dump()


# ---------------------------------------------------------------------------
# WorkflowRunner
# ---------------------------------------------------------------------------

# 致命系统级异常 — 让它冒泡终止整个 run, 不当节点失败处理.
# 收紧 M2: 之前 ``except Exception`` 把 OSError (disk full) / MemoryError /
# KeyboardInterrupt 也一起按节点失败处理, 用户 Ctrl+C 后 runner 还在 try 下一节点
# 是个糟糕的 UX. 这些必须立刻抛, 整 run abort.
_FATAL_EXC = (OSError, MemoryError, KeyboardInterrupt, SystemExit)


class WorkflowRunner:
    """同步执行器.

    Parameters
    ----------
    store : ArtifactStoreProto
        Artifact 落盘 / 读取后端. 必须满足 ``protocols.ArtifactStoreProto``
        (4 参 write + ``output_name`` 默认 ``"output"``).
    run_log_root : Path
        ``workflow_runs/`` 父目录, 用来拼 ``<root>/workflow_runs/<run_id>/run_log.jsonl``.
        通常 = ``store.root``, 但允许分离 (artifact / log 写不同盘).

    用法::

        store = ArtifactStore(root=Path("/tmp/wf"))
        runner = WorkflowRunner(store=store, run_log_root=Path("/tmp/wf"))
        result = runner.run(workflow_dict, run_id="wf_test_001")
    """

    def __init__(
        self, store: ArtifactStoreProto, run_log_root: Path
    ) -> None:
        if store is None:
            raise ValueError("WorkflowRunner.store 不能为 None — 必须传 ArtifactStore (或满足 ArtifactStoreProto 的实例)")
        if run_log_root is None:
            raise ValueError("WorkflowRunner.run_log_root 不能为 None")
        self.store: ArtifactStoreProto = store
        self.run_log_root: Path = Path(run_log_root)

    def run(
        self,
        workflow_or_dict: Workflow | dict,
        *,
        run_id: str | None = None,
        run_log: RunLogProto | None = None,
    ) -> RunResult:
        """执行 workflow, 返回 RunResult.

        ``workflow_or_dict`` 接受 ``Workflow`` 实例或 dict (Pydantic 会校验).

        失败节点不抛全局异常: 节点 status=FAILED, 下游 status=SKIPPED, run
        仍正常返回 (整体 status=FAILED). 这是 spec §5.3 的硬契约.

        ``CycleError`` / 未注册 type / DAG 形状错 都是 workflow 整体形状问题, 立即抛.
        ``OSError`` (磁盘满 / 写入失败) / ``MemoryError`` / ``KeyboardInterrupt`` /
        ``SystemExit`` 也立即冒泡 (整 run abort).
        """
        # ---- 1. 加载 + Pydantic 校验
        workflow = _coerce_workflow(workflow_or_dict)

        # ---- 2. 校验所有 node.type 已注册 + 引用合法 + 构 DAG + topo
        # type 缺失 / DAG 形状错 / 环 — 这些都是 workflow 整体的形状错误,
        # 立即抛 (跟节点级失败不同).
        for n in workflow.nodes:
            try:
                NodeRegistry.get(n.type)
            except NodeNotFoundError as e:
                # 用 ValueError 包一层带 workflow / node 上下文 (老 test 抓
                # ValueError + match=type名 仍然工作). 但保留 __cause__ 链到
                # NodeNotFoundError 给结构化捕获.
                raise ValueError(
                    f"Workflow {workflow.id!r} 节点 {n.id!r} type={n.type!r} 未在 NodeRegistry 注册: {e}"
                ) from e

        upstream = _build_dag(workflow)
        order = _topo_sort(upstream)  # 环时这里抛 CycleError

        # ---- 3. 装配执行环境
        run_id = run_id or uuid.uuid4().hex

        # 写 workflow.json + 创建节点目录
        # 路径约定: <run_log_root>/workflow_runs/<run_id>/
        run_dir = self.run_log_root / "workflow_runs" / run_id
        nodes_dir = run_dir / "nodes"
        nodes_dir.mkdir(parents=True, exist_ok=True)

        # workflow.json: 用户传 dict 也能复原. 序列化用 mode='json' 保证 Enum 字符串.
        workflow_json_path = run_dir / "workflow.json"
        workflow_json_path.write_text(
            workflow.model_dump_json(indent=2), encoding="utf-8"
        )

        # run_log: 默认走文件实现 (RunLog), 调用方可注入 mock/in-memory 实例
        if run_log is None:
            log: RunLogProto = RunLog(run_dir / "run_log.jsonl")
        else:
            log = run_log

        node_by_id: dict[str, Node] = {n.id: n for n in workflow.nodes}
        # 记录每个节点的 output URI (Phase 0 只支持单输出 "output")
        output_uris: dict[str, str] = {}
        # 跟踪节点状态, 决定下游 SKIPPED
        node_status: dict[str, NodeStatus] = {n.id: NodeStatus.PENDING for n in workflow.nodes}
        node_runs: list[NodeRun] = []

        run_start_ms = time.perf_counter()

        # ---- 4. 按拓扑顺序串行执行
        for node_id in order:
            n = node_by_id[node_id]

            # 4.1 若任一上游 FAILED / SKIPPED, 本节点 SKIPPED
            ups = upstream[node_id]
            blocked_by = [
                u for u in ups if node_status[u] in (NodeStatus.FAILED, NodeStatus.SKIPPED)
            ]
            if blocked_by:
                started = _iso_utc_now()
                nr = NodeRun(
                    run_id=run_id,
                    workflow_id=workflow.id,
                    node_id=node_id,
                    node_type=n.type,
                    status=NodeStatus.SKIPPED,
                    started_at=started,
                    ended_at=started,
                    duration_ms=0,
                    error=f"上游节点 {blocked_by} 未成功, 跳过本节点",
                )
                node_status[node_id] = NodeStatus.SKIPPED
                log.append(nr)
                node_runs.append(nr)
                continue

            # 4.2 收集 inputs (从 artifact_store 读)
            input_uris: dict[str, str] = {}
            inputs_payload: dict[str, Any] = {}
            input_collect_err: Exception | None = None
            try:
                for input_name, ref in n.inputs.items():
                    up_id, up_port = _parse_endpoint(ref)
                    # Phase 0 单输出: 用 up_id 直接查 URI 缓存
                    if up_id not in output_uris:
                        raise RuntimeError(
                            f"上游节点 {up_id!r} 没有可用 artifact (port={up_port})"
                        )
                    uri = output_uris[up_id]
                    input_uris[input_name] = uri
                    # store.read 走 (run_id, up_id, output_name) — Phase 0 单输出
                    # port 名固定 "output" (跟 mock_nodes 输入 key 解耦).
                    inputs_payload[input_name] = self.store.read(run_id, up_id, up_port)
            except _FATAL_EXC:
                # disk full / OOM / Ctrl+C → 整 run abort, 不当节点失败
                raise
            except Exception as e:  # noqa: BLE001
                input_collect_err = e

            # 4.3 算 input hash (即便 input 读失败, 也根据原始 params + URI 算)
            input_hash = _compute_input_hash(n.params, input_uris)

            started = _iso_utc_now()
            t0 = time.perf_counter()

            # 4.4 写 RUNNING
            running_nr = NodeRun(
                run_id=run_id,
                workflow_id=workflow.id,
                node_id=node_id,
                node_type=n.type,
                status=NodeStatus.RUNNING,
                input_hash=input_hash,
                started_at=started,
            )
            log.append(running_nr)

            # 4.5 若 input 收集已失败, 直接落 FAILED + 写 error.txt
            if input_collect_err is not None:
                duration_ms = int((time.perf_counter() - t0) * 1000)
                ended = _iso_utc_now()
                err_msg = f"{type(input_collect_err).__name__}: {input_collect_err}"
                self._write_error_txt(
                    run_id=run_id,
                    node_id=node_id,
                    error_msg=err_msg,
                    exc=input_collect_err,
                )
                nr = NodeRun(
                    run_id=run_id,
                    workflow_id=workflow.id,
                    node_id=node_id,
                    node_type=n.type,
                    status=NodeStatus.FAILED,
                    input_hash=input_hash,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    error=err_msg,
                )
                node_status[node_id] = NodeStatus.FAILED
                log.append(nr)
                node_runs.append(nr)
                continue

            # 4.6 取 RegisteredNode + 验 params + 执行
            # 节点失败收紧: 致命系统级异常冒泡, 其它 (含 NodeNotFoundError /
            # ValidationError / NodeExecutionError / 节点 compute 抛的 RuntimeError 等)
            # 走 FAILED.
            registered: RegisteredNode = NodeRegistry.get(n.type)
            try:
                validated_params = _validate_params(n.params, registered.params_model)
                raw_output = registered.compute(
                    params=validated_params, inputs=inputs_payload
                )
                final_output = _validate_outputs(raw_output, registered.outputs_model)
            except _FATAL_EXC:
                # 系统级灾难 → 整 run abort, 不留 NodeRun(FAILED) 半态
                raise
            except (NodeNotFoundError, ValidationError, NodeExecutionError, Exception) as e:  # noqa: BLE001
                duration_ms = int((time.perf_counter() - t0) * 1000)
                ended = _iso_utc_now()
                err_msg = f"{type(e).__name__}: {e}"
                self._write_error_txt(
                    run_id=run_id, node_id=node_id, error_msg=err_msg, exc=e
                )
                nr = NodeRun(
                    run_id=run_id,
                    workflow_id=workflow.id,
                    node_id=node_id,
                    node_type=n.type,
                    status=NodeStatus.FAILED,
                    input_hash=input_hash,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    error=err_msg,
                )
                node_status[node_id] = NodeStatus.FAILED
                log.append(nr)
                node_runs.append(nr)
                continue

            # 4.7 写 artifact + SUCCESS
            try:
                uri = self.store.write(run_id, node_id, "output", final_output)
            except _FATAL_EXC:
                raise
            except Exception as e:  # noqa: BLE001
                duration_ms = int((time.perf_counter() - t0) * 1000)
                ended = _iso_utc_now()
                err_msg = f"artifact_write_failed: {type(e).__name__}: {e}"
                self._write_error_txt(
                    run_id=run_id, node_id=node_id, error_msg=err_msg, exc=e
                )
                nr = NodeRun(
                    run_id=run_id,
                    workflow_id=workflow.id,
                    node_id=node_id,
                    node_type=n.type,
                    status=NodeStatus.FAILED,
                    input_hash=input_hash,
                    started_at=started,
                    ended_at=ended,
                    duration_ms=duration_ms,
                    error=err_msg,
                )
                node_status[node_id] = NodeStatus.FAILED
                log.append(nr)
                node_runs.append(nr)
                continue

            output_uris[node_id] = uri
            duration_ms = int((time.perf_counter() - t0) * 1000)
            ended = _iso_utc_now()
            nr = NodeRun(
                run_id=run_id,
                workflow_id=workflow.id,
                node_id=node_id,
                node_type=n.type,
                status=NodeStatus.SUCCESS,
                input_hash=input_hash,
                output_artifact_uri=uri,
                started_at=started,
                ended_at=ended,
                duration_ms=duration_ms,
            )
            node_status[node_id] = NodeStatus.SUCCESS
            log.append(nr)
            node_runs.append(nr)

        # ---- 5. 汇总
        overall_status = (
            NodeStatus.FAILED
            if any(ns == NodeStatus.FAILED for ns in node_status.values())
            else NodeStatus.SUCCESS
        )
        # artifacts_root: store.root + run_dir 的真实物理路径 (绝对 path 字符串).
        # 不再用 mem:// 占位.
        artifacts_root = str(
            (Path(self.store.root) / "workflow_runs" / run_id).as_posix()
        )
        total_ms = int((time.perf_counter() - run_start_ms) * 1000)

        return RunResult(
            run_id=run_id,
            workflow_id=workflow.id,
            status=overall_status,
            node_runs=node_runs,
            artifacts_root=artifacts_root,
            duration_ms=total_ms,
        )

    # ------------------------------------------------------------------
    # 内部 helper: 写 error.txt (spec §5.2.g)
    # ------------------------------------------------------------------
    def _write_error_txt(
        self,
        *,
        run_id: str,
        node_id: str,
        error_msg: str,
        exc: BaseException,
    ) -> None:
        """把节点失败的 traceback / 错误信息写到 ``<run_dir>/nodes/<node_id>/error.txt``.

        用 ``store.root`` 拼路径 (跟 artifacts.py 的 node_dir 约定一致), 不绕过
        store API 重新自己造目录约定. 写失败也不影响主流程 (best-effort), 避免在
        失败路径里再制造新错误把 NodeRun(FAILED) 也写不出.
        """
        try:
            node_dir = Path(self.store.root) / "workflow_runs" / run_id / "nodes" / node_id
            node_dir.mkdir(parents=True, exist_ok=True)
            tb = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            content = f"{error_msg}\n\n--- traceback ---\n{tb}"
            (node_dir / "error.txt").write_text(content, encoding="utf-8")
        except Exception:  # noqa: BLE001
            # 失败兜底 — 这里再抛就把主流程的 FAILED 状态也丢了.
            # 留给 stderr 风险 (Phase 0 妥协); Phase 1 加 logger.exception.
            pass


__all__ = [
    "WorkflowRunner",
    # 协议从 protocols.py re-export, 老代码 ``from runner import ArtifactStoreProto``
    # 不破 (作为 backward-compat 入口).
    "ArtifactStoreProto",
    "RunLogProto",
    "Payload",
]
