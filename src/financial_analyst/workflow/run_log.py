"""RunLog — append-only JSONL 节点执行日志.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §7.
Realign 阶段对齐契约 (workflow/protocols.py::RunLogProto):

- 构造改为 ``RunLog(path: Path)`` **单参**, ``path`` 是 run_log.jsonl 完整路径 (运行端
  拼好). 不再 ``RunLog(run_id, root)`` 自管目录拼接 — 跟 spec §7.1 一致, 调用方 (runner)
  决定 run_id → 目录结构的 mapping.
- **跨实例锁共享 (修 H5)**: 类级 ``_locks: dict[Path, Lock]`` + ``_locks_mutex``, 同 path
  不同实例 ``self._lock`` is 同一把锁. 这样 "log1 + log2 同 path 并发 append" (生产场景:
  同进程内 runner 实例化两个 RunLog 指向同一文件) 仍互斥, 没有交错半行.

每条记录是一次节点状态转换的不可变快照 (PENDING / RUNNING / SUCCESS / FAILED /
SKIPPED). 一行一个 ``NodeRun.model_dump_json()`` 输出 (Pydantic 已处理 Enum → str /
datetime → ISO / NaN・Inf → null, 跨进程读不出 Enum 反序列化坑).

错误处理纪律 (仿 ResearchArchive):
- ``read_all`` 缺文件 → []; 坏行 → 抛 (Phase 0 没坏行场景, 实际场景再加 warning skip).
- ``append`` 创目录失败 / 写盘失败 → 让异常抛, 调用方决定 retry vs 抛全局.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import ClassVar, Optional

from financial_analyst.workflow.schema import NodeRun, NodeStatus


class RunLog:
    """单个 workflow run 的 JSONL 日志.

    Parameters
    ----------
    path : Path
        ``run_log.jsonl`` 文件完整路径. 父目录可不存在, 第一次 ``append`` 会自动 mkdir.
        通常由 runner 拼成 ``<root>/workflow_runs/<run_id>/run_log.jsonl``.

    使用范式
    --------
    >>> log = RunLog(Path("/tmp/workflow_runs/abc123/run_log.jsonl"))
    >>> log.append(node_run)              # 线程安全, append-only
    >>> log.read_all()                    # 按写入顺序返回
    >>> log.latest_status("universe")     # 同 node_id 多次 append, 取最后一条
    """

    # 跨实例锁注册表. 同 ``path.resolve()`` 不同 ``RunLog`` 实例共享同一把 Lock,
    # 让两个 RunLog(p) 实例并发 append 仍互斥. 防御场景: 同进程内 runner 实例化
    # 两个 RunLog 指向同一文件 (热重载 / 并发 run 共享日志).
    _locks: ClassVar[dict[Path, threading.Lock]] = {}
    # 保护 _locks 自身的 setdefault — 否则两个线程同时进入构造可能各自创建一把 Lock.
    _locks_mutex: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, path: Path) -> None:
        # 不预检 path 是否为目录 — path 是 jsonl 文件路径, 父目录由 ``append`` lazy mkdir.
        # 但 path 本身不能空 (""): Path("") 拼成 PosixPath('.'), 后续 ``.resolve()`` 在
        # Windows 上会落到 cwd, 导致默默写到怪路径. 直接拒绝.
        # 用户可能传 str 也可能传 Path, 两种空形都要挡:
        if path is None:
            raise ValueError("RunLog.path 不能为 None")
        # str(Path("")) == '.', 不是 ''; 直接检原始入参的字符串形态.
        # 同时挡裸空字符串 `RunLog("")` (虽然签名是 Path, 用户可能传 str).
        raw = path if isinstance(path, str) else str(path)
        if raw == "" or raw == ".":
            raise ValueError("RunLog.path 不能为空 (拒绝 '' / Path(''))")
        self.path = Path(path)
        # parent 暴露给调用方 + 测试 (run_dir 概念)
        self.run_dir = self.path.parent

        # 跨实例共享 Lock: setdefault 在 _locks_mutex 保护下做, 保证幂等
        resolved_key = self.path.resolve() if self.path.is_absolute() else self.path.absolute().resolve()
        with RunLog._locks_mutex:
            self._lock = RunLog._locks.setdefault(resolved_key, threading.Lock())

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def append(self, node_run: NodeRun) -> None:
        """原子追加一行 JSON.

        - 用 ``model_dump_json()`` 让 Pydantic 处理:
          * Enum (``NodeStatus``) → 字面字符串 ("success" / "failed" ...)
          * datetime 字段 → ISO 8601 字符串
          * NaN / +Inf / -Inf → ``null`` (Pydantic v2 默认行为)
        - Lock 保护 "open + write + flush" 块, 防多线程并发交错半行字节.
          锁是按 ``path.resolve()`` 跨实例共享的, 同 path 不同 RunLog 实例 race 也不会
          交错.
        - 行末 ``\\n`` 是 JSONL 契约 (``read_all`` 按行 split).
        """
        # mkdir 放锁外: 多线程同时 mkdir(parents=True, exist_ok=True) 是安全的,
        # 锁的语义只针对"写同一文件不交错". 锁太大反而拖慢吞吐.
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Pydantic v2 model_dump_json: ensure_ascii=False by default, 兼容中文 node_id;
        # 不调用 json.dumps 二次包裹 (会双重转义).
        line = node_run.model_dump_json()

        with self._lock:
            # "a" 模式确保每次 append 从文件尾开始写, 不覆盖已有内容.
            # encoding=utf-8 强制, 不让 Windows 默认 cp1252 偷偷修改字节.
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                # flush 让 OS buffer 落盘, 防进程崩溃时丢最后几条.
                # 不做 fsync — Phase 0 单进程内 read_all 总能立即看到; 跨进程
                # durability 留 Phase 1 接真实 broker 时再加.
                fh.flush()

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def read_all(self) -> list[NodeRun]:
        """按写入顺序读回所有 ``NodeRun``. 文件不存在 → ``[]``.

        Phase 0 不做坏行容忍 — 真出现坏行说明 ``append`` 路径出 bug, 让异常显化
        比悄悄跳过更安全. Phase 1 接生产真实日志再考虑 logger.warning 跳过.
        """
        if not self.path.exists():
            return []

        # 一次性读完文件再 split: Phase 0 单 run 日志规模可控 (~几十~几百行),
        # 不流式读. Phase 1 单 run 节点数膨胀到上千再考虑分块.
        text = self.path.read_text(encoding="utf-8")

        runs: list[NodeRun] = []
        for raw in text.splitlines():
            # 空行跳过 (允许追加时手误 \n\n, 不当损坏处理).
            line = raw.strip()
            if not line:
                continue
            # model_validate(json.loads(...)) 让 Pydantic 还原 Enum / 校验形状.
            payload = json.loads(line)
            runs.append(NodeRun.model_validate(payload))
        return runs

    def latest_status(self, node_id: str) -> Optional[NodeStatus]:
        """同 node_id 多次出现 (PENDING → RUNNING → SUCCESS), 取最后一行的 status.

        Returns
        -------
        NodeStatus | None
            该 node_id 从未出现 → ``None``; 否则返回最近一条记录的 status.
        """
        # 反向扫: 大日志时比 forward 找最后一条快. Phase 0 日志小, 但 O(n) → O(k)
        # 的常数收益总是免费的.
        runs = self.read_all()
        for run in reversed(runs):
            if run.node_id == node_id:
                return run.status
        return None


__all__ = ["RunLog"]
