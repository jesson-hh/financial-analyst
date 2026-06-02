"""ArtifactStore — node 输出落盘 + 反序列化.

Spec: docs/superpowers/specs/2026-06-02-quantflow-phase0-design.md §6.
Realign 阶段对齐契约 (workflow/protocols.py::ArtifactStoreProto):

- ``write(run_id, node_id, output_name, payload) -> str``: 返**POSIX 相对路径字符串**
  (如 ``"workflow_runs/<run_id>/nodes/<node>/<output_name>.json"``), 而不是 ``Path`` /
  绝对路径. 这是 spec §6.3 URI 契约.
- ``output_name`` 现在是必填位置参数 (Phase 0 多输出节点也用这条 API), 默认值留在 ``read`` /
  ``exists`` 上 (向后兼容 + Phase 0 单输出场景仍然方便). 文件名规则: ``<output_name>.{json|parquet}``,
  不再硬编 ``output``.
- **写原子性 (修 H1)**: 用 ``tempfile.NamedTemporaryFile(dir=node_dir, delete=False)`` 落
  临时文件, 序列化成功后 ``os.replace`` 原子覆盖目标. 序列化失败 ``tmp.unlink(missing_ok=True)``
  避免半字节垃圾. 这样并发 + 进程崩溃都不会留下损坏文件给下次读到.
- **清旧扩展**: 同 ``(run_id, node_id, output_name)`` 二次写时, 先 ``unlink`` 当前已存在的
  另一扩展名 (json↔parquet), 避免一个节点先写 dict (output.json) 再写 DataFrame (output.parquet)
  时两个文件共存 → ``_find_output`` 拿到旧的 json 那条 stale 路径的幽灵 bug.

设计要点 (沿用):
- 按 payload 类型自动选格式: ``pd.DataFrame`` / ``pd.Series`` → parquet, 其它 → JSON.
- JSON 写时 NaN/Inf → ``null`` (sanitize + ``allow_nan=False`` 双保险). DataFrame 走
  parquet, parquet 原生支持 NaN, 不动.
- ``read`` 缺失抛 ``FileNotFoundError`` (清晰 IO 错, 不是 model 解析错).
- ``root`` 属性给 runner 用 (拼 artifacts_root 字符串).
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


_JSON_EXT = ".json"
_PARQUET_EXT = ".parquet"
_DEFAULT_OUTPUT_NAME = "output"


class ArtifactStore:
    """按 (run_id, node_id, output_name) 读写 node 输出.

    Parameters
    ----------
    root : Path
        ``workflow_runs/`` 的父目录. 内部会创建 ``workflow_runs/<run_id>/nodes/...``
        全树, 调用方不需要预先 ``mkdir``.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 公开 root 属性 — runner 在 RunResult.artifacts_root 用绝对路径字符串
    # ------------------------------------------------------------------
    @property
    def root(self) -> Path:
        """``workflow_runs/`` 的父目录 (绝对 Path)."""
        return self._root

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------
    def _run_dir(self, run_id: str) -> Path:
        return self._root / "workflow_runs" / run_id

    def _node_dir(self, run_id: str, node_id: str) -> Path:
        return self._run_dir(run_id) / "nodes" / node_id

    def _find_output(
        self, run_id: str, node_id: str, output_name: str = _DEFAULT_OUTPUT_NAME
    ) -> Path | None:
        """在 node 目录里找 ``<output_name>.json`` 或 ``<output_name>.parquet`` (二选一存在).

        实际写入永远只留一种扩展 (write 会先 unlink 另一种), 但 ``_find_output`` 仍按
        两种扩展依次探测, 容忍历史目录或外部工具留下的混合状态.
        """
        node_dir = self._node_dir(run_id, node_id)
        if not node_dir.is_dir():
            return None
        for ext in (_JSON_EXT, _PARQUET_EXT):
            candidate = node_dir / f"{output_name}{ext}"
            if candidate.is_file():
                return candidate
        return None

    def _to_relative_posix(self, path: Path) -> str:
        """绝对 ``path`` → 相对 ``self._root`` 的 POSIX 字符串.

        Windows 上 ``Path.relative_to`` 给的也是 PurePosixPath-compatible, 再走 ``as_posix``
        把 ``\\`` 翻成 ``/``. 这样跨平台 URI 字符串恒定.
        """
        return path.resolve().relative_to(self._root.resolve()).as_posix()

    # ------------------------------------------------------------------
    # 公共 API — 与 protocols.ArtifactStoreProto 对齐
    # ------------------------------------------------------------------
    def exists(
        self, run_id: str, node_id: str, output_name: str = _DEFAULT_OUTPUT_NAME
    ) -> bool:
        """``(run_id, node_id, output_name)`` 是否已有 output 落盘."""
        return self._find_output(run_id, node_id, output_name) is not None

    def write(
        self,
        run_id: str,
        node_id: str,
        output_name: str,
        payload: Any,
    ) -> str:
        """落盘 node 输出, 返回**相对 root 的 POSIX 路径字符串**.

        URI 形如 ``"workflow_runs/<run_id>/nodes/<node>/<output_name>.json"``,
        给 runner 写 ``NodeRun.output_artifact_uri`` 用.

        类型路由 (spec §6.2):
        - ``pd.DataFrame`` → parquet (pyarrow engine)
        - ``pd.Series``    → 转 ``DataFrame`` (保留 name) → parquet
        - ``dict`` / ``list`` / ``str`` / ``int`` / ``float`` / ``bool`` / ``None``
          → JSON (``ensure_ascii=False``, ``indent=2``, NaN/Inf → null)
        - 其它 (``np.ndarray`` 等) → ``TypeError`` (节点应自己转)

        原子性: 落临时文件 → ``os.replace`` 原子覆盖目标. 序列化失败清理临时文件.
        清旧扩展: 写前 unlink 当前 ``(run_id, node_id, output_name)`` 的其它扩展.
        """
        node_dir = self._node_dir(run_id, node_id)
        node_dir.mkdir(parents=True, exist_ok=True)

        # 清旧扩展: 防 dict (.json) ↔ DataFrame (.parquet) 同名共存
        # 这两个分别是 JSON / parquet 路径, 必须 *两个都尝试 unlink*, 不依赖
        # payload 类型 (类型可能在 write 之间变化, 比如同一节点先 dict 再 DataFrame).
        for ext in (_JSON_EXT, _PARQUET_EXT):
            stale = node_dir / f"{output_name}{ext}"
            stale.unlink(missing_ok=True)

        # DataFrame: parquet
        if isinstance(payload, pd.DataFrame):
            final = node_dir / f"{output_name}{_PARQUET_EXT}"
            self._atomic_write_parquet(payload, final, node_dir)
            return self._to_relative_posix(final)

        # Series: 转 DataFrame (保 name) -> parquet
        if isinstance(payload, pd.Series):
            final = node_dir / f"{output_name}{_PARQUET_EXT}"
            # ``to_frame`` 保留 Series.name (None 时 column = 0).
            # parquet 反序列化时即可 ``pd.read_parquet(...).iloc[:, 0]`` 取回。
            self._atomic_write_parquet(payload.to_frame(), final, node_dir)
            return self._to_relative_posix(final)

        # JSON 允许的类型 (含嵌套 dict / list)
        # bool 是 int 子类要注意, 但 isinstance 已分别处理两者 (Python 内
        # bool 在 int 之前不重要 — json.dumps 区分两者).
        if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None:
            final = node_dir / f"{output_name}{_JSON_EXT}"
            sanitized = _sanitize_for_json(payload)
            self._atomic_write_json(sanitized, final, node_dir)
            return self._to_relative_posix(final)

        # 其它类型显式拒绝, 节点自己 cast (spec §6.2 最后一行)
        raise TypeError(
            f"ArtifactStore.write 不支持 payload 类型 {type(payload).__name__!r}. "
            "节点请先转成 dict / list / 标量 / DataFrame / Series."
        )

    def read(
        self, run_id: str, node_id: str, output_name: str = _DEFAULT_OUTPUT_NAME
    ) -> Any:
        """读回 node 输出. 缺失抛 ``FileNotFoundError``.

        Returns
        -------
        Any
            - JSON 文件 → ``json.load`` 出来的对象 (dict / list / 标量)
            - parquet 文件 → ``pd.DataFrame``
              (写入时 Series 已被转 DataFrame, 这里不还原回 Series — 形状信息
              够用且双方都明白; 真要 Series 调用方 ``df.iloc[:, 0]`` 一行取回。)
        """
        path = self._find_output(run_id, node_id, output_name)
        if path is None:
            raise FileNotFoundError(
                f"ArtifactStore: 找不到 ({run_id!r}, {node_id!r}, {output_name!r}) 的 output. "
                f"node_dir={self._node_dir(run_id, node_id)}"
            )
        if path.suffix == _JSON_EXT:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        if path.suffix == _PARQUET_EXT:
            return pd.read_parquet(path, engine="pyarrow")
        # 不会到这里, _find_output 只匹配两个扩展名
        raise RuntimeError(f"ArtifactStore: 未知 artifact 扩展名 {path.suffix!r}")

    # ------------------------------------------------------------------
    # 原子写入 helpers
    # ------------------------------------------------------------------
    def _atomic_write_json(self, sanitized: Any, final: Path, node_dir: Path) -> None:
        """JSON 原子写: tmp 文件 → os.replace.

        失败 (序列化抛 / write 抛) 清理临时文件. 不向 caller 提供 partial state.
        """
        # delete=False 在 Windows 上必须 — TemporaryFile 上下文退出会删文件,
        # 我们要的是手动控制清理.
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=node_dir,
            prefix=f".{final.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            try:
                # allow_nan=False: 万一 sanitize 漏了, fail-fast 不写出非法 JSON
                json.dump(sanitized, tmp, ensure_ascii=False, indent=2, allow_nan=False)
                tmp.flush()
                os.fsync(tmp.fileno())
            finally:
                tmp.close()
            # os.replace 是 POSIX/Windows 都原子的目标替换. 跨文件系统会抛 — Phase 0
            # tmp 与 final 同目录, 必同文件系统, 不会有跨设备问题.
            os.replace(tmp_path, final)
        except Exception:
            # 任何阶段失败都清掉临时文件
            tmp_path.unlink(missing_ok=True)
            raise

    def _atomic_write_parquet(self, df: pd.DataFrame, final: Path, node_dir: Path) -> None:
        """parquet 原子写: 落临时 parquet → os.replace.

        pyarrow 不接受 file-like 一步写, 需要 ``to_parquet(path)`` 走文件路径, 所以
        临时文件用 ``NamedTemporaryFile(delete=False)`` 立刻 close, 然后传 path 给 pandas.
        """
        tmp = tempfile.NamedTemporaryFile(
            mode="wb",
            dir=node_dir,
            prefix=f".{final.name}.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            df.to_parquet(tmp_path, engine="pyarrow", index=True)
            os.replace(tmp_path, final)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise


# ---------------------------------------------------------------------------
# JSON sanitize: NaN / Inf -> None (递归)
# ---------------------------------------------------------------------------


def _sanitize_for_json(obj: Any) -> Any:
    """递归把 NaN / Inf 浮点替换成 None, 让 ``allow_nan=False`` 能放行.

    JSON spec 不允许 NaN / Inf 字面量, Python ``json.dumps`` 默认 ``allow_nan=True``
    会写出 ``NaN`` / ``Infinity``, 跨语言 (浏览器 / Go / Rust) 解析直接崩。
    严格按 spec 走 → 浮点特殊值统一 ``null``。

    实现细节:
    - 处理 dict / list / tuple 递归。
    - 处理 ``numpy.floating`` (节点可能直接塞 ``np.float64``)。
    - 处理 ``numpy.integer`` → ``int`` (Python json 不认 numpy int).
    - 处理 ``numpy.bool_`` → ``bool``.
    - ``None`` / ``str`` / ``bool`` / 普通 ``int`` 原样返回。
    """
    if obj is None:
        return None

    # bool 必须在 int 之前判 (bool 是 int 子类)
    if isinstance(obj, bool):
        return obj

    # numpy 标量先转 Python 原生
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        f = float(obj)
        if math.isnan(f) or math.isinf(f):
            return None
        return f

    # Python float
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj

    if isinstance(obj, int):
        return obj

    if isinstance(obj, str):
        return obj

    if isinstance(obj, dict):
        # JSON key 必须是 string. 数字 key Python json 会自动 str(), 跟样;
        # 这里复用同样的行为, 但显式处理一遍避免漏 sanitize value.
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]

    # 其它类型 (set / Path / datetime / ...) 不动 — 让 json.dump 自己抱怨,
    # 这样调用方能立刻意识到漏了类型, 而不是被 sanitize 静默吞掉.
    return obj


__all__ = ["ArtifactStore"]
