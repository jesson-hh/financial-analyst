# -*- coding: utf-8 -*-
"""工作流持久化 store(guanlan 自有)—— 落 ``.data/workflows/<id>.json``。

仿 ``guanlan_v2/cards/store.py`` 的根解析与可注入 root 范式,但落 **JSON**(不是 .md),
且 ``id`` / ``ts`` 由后端用 ``uuid`` / ``datetime`` 生成(对齐用户指令:存储的地方由后端生成 id/ts)。

每个工作流一个文件::

    .data/workflows/<id>.json  ==  {id, name, ts, scope, workspace_id, graph:{nodes, edges}}

旧文件缺少 ``scope/workspace_id`` 时仍按全局工作流读取。空间私有实例继续使用
随机 id 的平铺文件，但所有 list/get/delete 都先核对记录中的 owner，绝不靠目录名
或调用方传入的路径定位。

- 根解析(照 cards/store.py:21-31):先读 ``GUANLAN_WORKFLOW_ROOT`` 环境覆盖;
  缺省 ``<repo>/.data/workflows``(本文件在 ``guanlan_v2/workflow/`` 下,parent×3 = 仓库根)。
- 读取容错(照 factorlib/store.py:51-55 ``_read_json_dir``):坏 JSON ``try/except`` 跳过不崩。
- 纯本地 JSON,**不碰引擎、不碰 stock_data、不经 get_data_paths**;运行期落盘只写 ``.data/``。

被 ``guanlan_v2.workflow.api.build_workflow_router`` 调用。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.workflow.migrations import (
    WORKFLOW_SCHEMA_VERSION,
    migrate_graph,
)

# 仅用于 id 校验(防路径穿越);文件名直接用后端生成的 uuid hex,天然安全。
_SAFE = re.compile(r"[^0-9A-Za-z_\-]")
_WORKSPACE_ID = re.compile(r"^rw_(?:general|[0-9a-f]{12})$")


def _workspace_id(value: Optional[str]) -> Optional[str]:
    """Validate a workspace namespace without turning it into a filesystem path."""
    if value is None:
        return None
    workspace_id = str(value).strip()
    if not _WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("workspace_id 格式不合法")
    return workspace_id


def _scope_of(data: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """Old records have no scope fields and therefore remain global."""
    workspace_id = data.get("workspace_id")
    if data.get("scope") == "workspace":
        if isinstance(workspace_id, str) and _WORKSPACE_ID.fullmatch(workspace_id):
            return "workspace", workspace_id
        return "invalid", None  # malformed private metadata must never leak global
    return "global", None


def _default_root() -> Path:
    """解析工作流存储根(照 cards/store.py:21-31):

        1. $GUANLAN_WORKFLOW_ROOT           (显式覆盖)
        2. <repo>/.data/workflows           (默认, store.py 上溯三级到 guanlan-v2 根)
    """
    env = os.environ.get("GUANLAN_WORKFLOW_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    # store.py 在 guanlan_v2/workflow/ 下 → parent×3 = guanlan-v2 仓库根
    return Path(__file__).resolve().parent.parent.parent / ".data" / "workflows"


class WorkflowStore:
    """工作流存储. 每个工作流一个 ``<id>.json``(后端生成 id/ts);root 可注入便于测试."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_root()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        """Atomically replace one record so an interrupted save cannot corrupt it."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent),
        )
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    def save(self, name: str, graph: dict,
             workspace_id: Optional[str] = None) -> dict:
        """生成 id/ts、原子写 ``<id>.json``,返回完整记录(含 graph)。

        - ``id`` = ``"w" + uuid.uuid4().hex[:12]``(天然安全文件名,无路径穿越)。
        - ``ts`` = 毫秒时间戳(对齐前端 ``Date.now()`` 语义,HistoryModal 的 wfAgo 吃毫秒)。
        - ``graph`` 只保留 ``{nodes, edges}`` 两键(缺省空数组)。
        - **同名覆盖**(SaveModal 文案「同名将覆盖」的服务端实现,对齐前端
          localStorage 按 name 去重语义):已有同名记录 → 复用其中最新一条的 id
          覆盖原文件,其余同名旧副本一并删除,不再堆副本。
        """
        graph, migration = migrate_graph(graph or {})
        workspace_id = _workspace_id(workspace_id)
        nm = (name or "").strip()
        wid = ""
        if nm:
            # Name replacement is namespace-local.  Saving a workspace copy of
            # a global template never overwrites or deletes the template.
            same = [
                r for r in self.list(workspace_id=workspace_id)
                if (r.get("name") or "").strip() == nm
            ]
            if same:
                wid = str(same[0].get("id") or "")          # list() 按 ts 倒序 → [0] 最新
                for extra in same[1:]:                       # 多余同名旧副本清掉
                    self.delete(str(extra.get("id") or ""), workspace_id=workspace_id)
        if not wid or _SAFE.search(wid) or Path(wid).name != wid:
            wid = "w" + uuid.uuid4().hex[:12]
        ts = int(datetime.now().timestamp() * 1000)
        rec: Dict[str, Any] = {
            "id": wid,
            "name": (name or "").strip() or ("工作流 " + wid),
            "ts": ts,
            "scope": "workspace" if workspace_id else "global",
            "workspace_id": workspace_id,
            "schema_version": WORKFLOW_SCHEMA_VERSION,
            "migration": migration,
            "graph": {
                "nodes": graph.get("nodes", []) or [],
                "edges": graph.get("edges", []) or [],
            },
        }
        self._write_json(self.root / f"{wid}.json", rec)
        return rec

    def list(self, workspace_id: Optional[str] = None,
             include_global: bool = False) -> List[dict]:
        """列出全部工作流(回全量含 graph,按 ts 倒序)。坏 JSON 跳过不崩。

        回全量 ``{id,name,ts,graph:{nodes,edges}}``:工作流体量小(几十节点),
        省一次二次 GET;前端 HistoryModal 节点数/链路文案可立即正确。
        """
        workspace_id = _workspace_id(workspace_id)
        out: List[dict] = []
        if not self.root.is_dir():
            return out
        for fp in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001  坏文件跳过(照 factorlib/store.py:51-55)
                continue
            if not isinstance(data, dict):
                continue
            scope, owner = _scope_of(data)
            own_workspace = workspace_id is not None and scope == "workspace" \
                and owner == workspace_id
            visible_global = scope == "global" and (
                workspace_id is None or include_global
            )
            if not (own_workspace or visible_global):
                continue
            g = data.get("graph") or {}
            out.append({
                "id": data.get("id") or fp.stem,
                "name": data.get("name") or "",
                "ts": data.get("ts") or 0,
                "scope": scope,
                "workspace_id": owner,
                "read_only": bool(workspace_id is not None and scope == "global"),
                "is_template": bool(workspace_id is not None and scope == "global"),
                "schema_version": data.get("schema_version") or 1,
                "migration": data.get("migration") or {},
                "validation": data.get("validation") or {},
                "graph": {
                    "nodes": g.get("nodes", []) or [],
                    "edges": g.get("edges", []) or [],
                },
            })
        out.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        return out

    def get(self, wid: str, workspace_id: Optional[str] = None,
            include_global: bool = False) -> Optional[dict]:
        """取单个工作流全量;id 安全化防穿越;不存在 / 坏 JSON 回 None。"""
        workspace_id = _workspace_id(workspace_id)
        if not wid or _SAFE.search(wid) or Path(wid).name != wid:
            return None
        p = self.root / f"{wid}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(data, dict):
            return None
        scope, owner = _scope_of(data)
        if workspace_id is None:
            if scope != "global":
                return None
        elif not (scope == "workspace" and owner == workspace_id) \
                and not (scope == "global" and include_global):
            return None
        g = data.get("graph") or {}
        data["graph"] = {
            "nodes": g.get("nodes", []) or [],
            "edges": g.get("edges", []) or [],
        }
        data["scope"] = scope
        data["workspace_id"] = owner
        data["read_only"] = bool(workspace_id is not None and scope == "global")
        data["is_template"] = bool(workspace_id is not None and scope == "global")
        data["schema_version"] = data.get("schema_version") or 1
        data["migration"] = data.get("migration") or {}
        data["validation"] = data.get("validation") or {}
        return data

    def delete(self, wid: str, workspace_id: Optional[str] = None) -> bool:
        """删除单个工作流;id 安全化防穿越;存在则删并回 True,不存在回 False。"""
        workspace_id = _workspace_id(workspace_id)
        if not wid or _SAFE.search(wid) or Path(wid).name != wid:
            return False
        p = self.root / f"{wid}.json"
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # corrupted records are not deletable through a scope
            return False
        if not isinstance(data, dict):
            return False
        scope, owner = _scope_of(data)
        if workspace_id is None and scope != "global":
            return False
        if workspace_id is not None and not (
                scope == "workspace" and owner == workspace_id):
            return False
        p.unlink(missing_ok=True)
        return True
