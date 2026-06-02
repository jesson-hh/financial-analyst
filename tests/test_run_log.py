"""RunLog (workflow/run_log.py) 测试.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §7.
Realign 阶段对齐契约 (workflow/protocols.py::RunLogProto):
- 构造改为 ``RunLog(path: Path)`` **单参**, path 是 run_log.jsonl 完整路径.
- 跨实例锁共享 (修 H5): 同 path 不同 RunLog 实例并发 append 仍互斥.

覆盖契约:
1. ``append`` 3 条 → ``read_all`` 保持写入顺序 (JSONL 行序 = 时间序).
2. 同 ``node_id`` append 两次 → ``latest_status`` 取最后一条 (PENDING → SUCCESS 转换).
3. 单实例并发 ``append`` (8 线程 × 4 条 = 32 条) 不丢条数, 无半行损坏.
4. **跨实例**并发 ``append`` (多 RunLog 实例共享 path) 仍互斥, 不交错.
5. 文件追加而非覆盖 — 同一 path 重建 ``RunLog`` 后, append 在已有行尾继续追加.
6. RunLogProto runtime_checkable 满足.

线程数 8 是 Phase 0 单机内合理上限. 总 32 条小于 OS pipe buffer 也小于 SSD 单次
flush 上限, 不引入 IO 瓶颈干扰.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from financial_analyst.workflow.protocols import RunLogProto
from financial_analyst.workflow.run_log import RunLog
from financial_analyst.workflow.schema import NodeRun, NodeStatus


# ---------------------------------------------------------------------------
# 测试 helpers
# ---------------------------------------------------------------------------


def _make_run(
    node_id: str,
    status: NodeStatus = NodeStatus.SUCCESS,
    *,
    run_id: str = "r1",
    workflow_id: str = "wf",
    node_type: str = "data.constant_universe",
    started_at: str = "2026-06-02T01:00:00Z",
    **kwargs,
) -> NodeRun:
    """构造一个 NodeRun (测试默认值). kwargs 透传给 NodeRun 覆盖其它字段."""
    return NodeRun(
        run_id=run_id,
        workflow_id=workflow_id,
        node_id=node_id,
        node_type=node_type,
        status=status,
        started_at=started_at,
        **kwargs,
    )


def _log_path(tmp_path: Path, name: str = "run_log.jsonl") -> Path:
    """tmp_path 下的 JSONL 文件路径 (父目录不存在 — append 会 lazy mkdir)."""
    return tmp_path / "workflow_runs" / "run-x" / name


# ---------------------------------------------------------------------------
# 0. Protocol 兼容性
# ---------------------------------------------------------------------------


def test_runlog_satisfies_protocol(tmp_path: Path) -> None:
    """RunLog 实例必须满足 runtime_checkable 的 RunLogProto."""
    log = RunLog(_log_path(tmp_path))
    assert isinstance(log, RunLogProto)


# ---------------------------------------------------------------------------
# 1. append + read_all 保持顺序
# ---------------------------------------------------------------------------


def test_append_three_records_read_all_preserves_order(tmp_path: Path) -> None:
    """JSONL 是 append-only 文件, 行序 = 写入序 = read_all 返回序."""
    log = RunLog(_log_path(tmp_path))

    a = _make_run("universe", NodeStatus.RUNNING)
    b = _make_run("zeros", NodeStatus.SUCCESS)
    c = _make_run("rowcount", NodeStatus.FAILED, error="ValueError: x")

    log.append(a)
    log.append(b)
    log.append(c)

    runs = log.read_all()
    assert len(runs) == 3
    # node_id 顺序 = 写入顺序
    assert [r.node_id for r in runs] == ["universe", "zeros", "rowcount"]
    # status / error 也要保真
    assert runs[0].status == NodeStatus.RUNNING
    assert runs[1].status == NodeStatus.SUCCESS
    assert runs[2].status == NodeStatus.FAILED
    assert runs[2].error == "ValueError: x"


def test_read_all_returns_empty_when_file_missing(tmp_path: Path) -> None:
    """文件不存在 (从未 append) → 空 list, 不抛."""
    log = RunLog(_log_path(tmp_path, "empty.jsonl"))
    assert log.read_all() == []
    # 文件确实没创建
    assert not log.path.exists()


# ---------------------------------------------------------------------------
# 2. latest_status — 同 node_id 多次 append 取最后一条
# ---------------------------------------------------------------------------


def test_latest_status_returns_last_record_for_same_node_id(tmp_path: Path) -> None:
    """节点正常生命周期 = PENDING → RUNNING → SUCCESS, latest_status 取末态."""
    log = RunLog(_log_path(tmp_path))
    log.append(_make_run("universe", NodeStatus.PENDING, started_at="2026-06-02T01:00:00Z"))
    log.append(_make_run("universe", NodeStatus.RUNNING, started_at="2026-06-02T01:00:01Z"))
    log.append(_make_run("universe", NodeStatus.SUCCESS, started_at="2026-06-02T01:00:02Z"))

    assert log.latest_status("universe") == NodeStatus.SUCCESS


def test_latest_status_two_appends_takes_latter(tmp_path: Path) -> None:
    """spec 用例: 同 node_id append 两次, latest_status 取后者."""
    log = RunLog(_log_path(tmp_path))
    log.append(_make_run("zeros", NodeStatus.PENDING))
    log.append(_make_run("zeros", NodeStatus.FAILED, error="RuntimeError: boom"))

    assert log.latest_status("zeros") == NodeStatus.FAILED


def test_latest_status_returns_none_when_node_unseen(tmp_path: Path) -> None:
    """没出现过的 node_id → None (调用方判 None 决定 default)."""
    log = RunLog(_log_path(tmp_path))
    log.append(_make_run("universe", NodeStatus.SUCCESS))
    assert log.latest_status("not-there") is None


def test_latest_status_multiple_nodes_independent(tmp_path: Path) -> None:
    """多 node_id 交错 append, latest_status 各自独立取末态."""
    log = RunLog(_log_path(tmp_path))
    log.append(_make_run("a", NodeStatus.RUNNING))
    log.append(_make_run("b", NodeStatus.RUNNING))
    log.append(_make_run("a", NodeStatus.SUCCESS))
    log.append(_make_run("b", NodeStatus.FAILED))

    assert log.latest_status("a") == NodeStatus.SUCCESS
    assert log.latest_status("b") == NodeStatus.FAILED


# ---------------------------------------------------------------------------
# 3. 单实例并发 append (8 线程 × 4 条) 不丢条数
# ---------------------------------------------------------------------------


def test_concurrent_append_no_lost_lines(tmp_path: Path) -> None:
    """8 线程并发 append, 每线程 4 条, 总共 32 条; lock 保证不丢不损坏.

    每条记录的 node_id 编码 (worker_idx, write_idx), 便于校验全集存在.
    """
    log = RunLog(_log_path(tmp_path))

    n_threads = 8
    writes_per_thread = 4
    expected_total = n_threads * writes_per_thread

    barrier = threading.Barrier(n_threads)  # 让 8 线程同时起跑, 制造真实竞争窗口

    def worker(worker_idx: int) -> None:
        barrier.wait()  # 等所有线程就绪 → 同时冲锁, 最大化交错概率
        for write_idx in range(writes_per_thread):
            log.append(
                _make_run(
                    node_id=f"w{worker_idx}-{write_idx}",
                    status=NodeStatus.SUCCESS,
                )
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 1. 总条数 = 32 (无丢)
    runs = log.read_all()
    assert len(runs) == expected_total, (
        f"expected {expected_total}, got {len(runs)} — concurrent append lost records"
    )

    # 2. 全集存在 (node_id 编码 worker × write_idx 是唯一组合)
    expected_ids = {
        f"w{w}-{i}" for w in range(n_threads) for i in range(writes_per_thread)
    }
    actual_ids = {r.node_id for r in runs}
    assert actual_ids == expected_ids

    # 3. 文件物理上每行都是合法 JSON (锁防交错半行)
    raw = log.path.read_text(encoding="utf-8").splitlines()
    assert len(raw) == expected_total
    for line in raw:
        # json.loads 能解析 = 没有半行损坏 (交错写最常见症状)
        payload = json.loads(line)
        assert payload["node_id"].startswith("w")
        assert payload["status"] == "success"  # Enum 必须落字符串


# ---------------------------------------------------------------------------
# 4. 跨实例并发 append — 修 H5 核心场景
# ---------------------------------------------------------------------------


def test_cross_instance_lock_shared_no_interleaved_lines(tmp_path: Path) -> None:
    """两个 RunLog 实例指向同 path, 并发 append 必须互斥.

    场景: 同进程内, 不同代码路径各自实例化 RunLog(path) (热重载 / 并发 run 共享日志).
    若每个实例各持自己的 Lock, 写就会交错半行. 跨实例锁共享 (类级 _locks dict)
    解决这点.
    """
    path = _log_path(tmp_path)

    # 两个独立 RunLog 实例, 指向同一 path
    log_a = RunLog(path)
    log_b = RunLog(path)

    # 应该共享同一把锁 (类级 _locks 缓存)
    assert log_a._lock is log_b._lock, (
        "同 path 不同 RunLog 实例必须共享 Lock, 否则跨实例并发写会交错"
    )

    n_per_instance = 50
    barrier = threading.Barrier(2)

    def worker(log: RunLog, prefix: str) -> None:
        barrier.wait()
        for i in range(n_per_instance):
            log.append(
                _make_run(node_id=f"{prefix}-{i}", status=NodeStatus.SUCCESS)
            )

    t_a = threading.Thread(target=worker, args=(log_a, "A"))
    t_b = threading.Thread(target=worker, args=(log_b, "B"))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    # 1. 总条数 = 100, 无丢
    raw = path.read_text(encoding="utf-8").splitlines()
    assert len(raw) == 2 * n_per_instance

    # 2. 每行可独立解析 (无半行损坏 — 锁防交错的核心证据)
    for line in raw:
        payload = json.loads(line)
        assert payload["node_id"].startswith(("A-", "B-"))


# ---------------------------------------------------------------------------
# 5. 文件追加 (不覆盖) — 重建 RunLog 实例继续 append
# ---------------------------------------------------------------------------


def test_append_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    """第二个 RunLog 实例 append 应该续在第一个的后面, 不抹掉旧行."""
    path = _log_path(tmp_path)

    # 第一阶段: 写 2 条
    log1 = RunLog(path)
    log1.append(_make_run("a", NodeStatus.RUNNING))
    log1.append(_make_run("a", NodeStatus.SUCCESS))

    # 第二阶段: 新实例 (模拟进程重启), 同 path
    log2 = RunLog(path)

    # 重启前先看到 2 条历史
    assert len(log2.read_all()) == 2
    assert log2.latest_status("a") == NodeStatus.SUCCESS

    # 续写 2 条 — 应该追加, 不覆盖
    log2.append(_make_run("b", NodeStatus.RUNNING))
    log2.append(_make_run("b", NodeStatus.SUCCESS))

    final = log2.read_all()
    assert len(final) == 4, "新 RunLog 实例 append 覆盖了旧文件而非追加"
    assert [r.node_id for r in final] == ["a", "a", "b", "b"]
    assert [r.status for r in final] == [
        NodeStatus.RUNNING,
        NodeStatus.SUCCESS,
        NodeStatus.RUNNING,
        NodeStatus.SUCCESS,
    ]


# ---------------------------------------------------------------------------
# 6. 边界 / 序列化契约
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_path(tmp_path: Path) -> None:
    """空 path 立即抛 — 防默默写到怪路径."""
    with pytest.raises(ValueError, match="path"):
        RunLog(Path(""))


def test_append_creates_parent_dir_lazily(tmp_path: Path) -> None:
    """构造时不创目录, 第一次 append 才创 (省 IO + 测试更干净)."""
    path = _log_path(tmp_path)
    log = RunLog(path)
    assert not log.run_dir.exists()

    log.append(_make_run("x", NodeStatus.SUCCESS))
    assert log.run_dir.exists()
    assert log.path.exists()


def test_jsonl_format_one_record_per_line(tmp_path: Path) -> None:
    """JSONL 契约: 每行恰好是一条 NodeRun 的 model_dump_json 输出 + \\n.

    强校验: 文件长度 = sum(json + \\n), 不能有多余字节.
    """
    log = RunLog(_log_path(tmp_path))
    runs = [
        _make_run("a", NodeStatus.RUNNING),
        _make_run("a", NodeStatus.SUCCESS, duration_ms=42),
    ]
    for r in runs:
        log.append(r)

    text = log.path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) == 2

    # 每行可以独立 json.loads
    payload0 = json.loads(lines[0])
    payload1 = json.loads(lines[1])
    assert payload0["status"] == "running"
    assert payload1["status"] == "success"
    assert payload1["duration_ms"] == 42

    # 文件以 \n 结尾 (POSIX line-by-line tail 友好)
    assert text.endswith("\n")
