"""Research workspace metadata and one-time folder grants.

Workspace metadata is deliberately separate from conversation events.  Managed
roots live below the console data directory; external roots can only be bound
with a short-lived, one-time grant written by the trusted desktop folder picker.
Archiving metadata never removes either kind of root directory.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


GENERAL_WORKSPACE_ID = "rw_general"
WORKSPACE_KINDS = frozenset({"general", "stock", "etf"})
ROOT_MODES = frozenset({"managed", "external"})
AGENT_MODES = frozenset({"research", "collaborative", "autonomous"})

_WORKSPACE_ID_RE = re.compile(r"^rw_[0-9a-f]{12}$")
_FOLDER_GRANT_RE = re.compile(r"^fg_[0-9a-f]{24}$")
_A_SHARE_RE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_A_SHARE_SUFFIX_RE = re.compile(r"^(\d{6})\.(SH|SZ|BJ)$")
_HK_RE = re.compile(r"^(?:HK)?(\d{5})$")
_OTHER_CODE_RE = re.compile(r"^[A-Z][A-Z0-9._:-]{0,19}$")


class WorkspaceError(ValueError):
    """A safe, user-facing workspace validation error."""


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceAmbiguous(WorkspaceError):
    """More than one active security workspace owns the same canonical code."""


class FolderGrantError(WorkspaceError):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_security_code(value: Any) -> str:
    """Return a compact canonical code without guessing beyond common markets."""
    code = re.sub(r"\s+", "", str(value or "")).upper()
    if not code:
        return ""
    suffix = _A_SHARE_SUFFIX_RE.fullmatch(code)
    if suffix:
        return suffix.group(2) + suffix.group(1)
    if _A_SHARE_RE.fullmatch(code):
        return code
    if code.isdigit() and len(code) == 6:
        # A-share conventional inference.  Explicit SH/SZ/BJ is retained when
        # supplied; bare codes stay convenient for the common UI path.
        # Beijing Exchange also uses the 920xxx block.  Check that specific
        # prefix before the broader Shanghai 9xxxxx convention.
        if code.startswith(("4", "8", "920")):
            return "BJ" + code
        if code.startswith(("5", "6", "9")):
            return "SH" + code
        return "SZ" + code
    hk = _HK_RE.fullmatch(code)
    if hk:
        return "HK" + hk.group(1)
    if _OTHER_CODE_RE.fullmatch(code):
        return code
    raise WorkspaceError("证券代码格式不合法")


def _clean_text(value: Any, *, field: str, limit: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise WorkspaceError(f"{field}不能为空")
    return text[:limit]


class WorkspaceStore:
    """Thread-safe JSON store for research workspace metadata."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.workspaces_dir = self.root / "workspaces"
        self.managed_roots_dir = self.root / "workspace_files"
        self.folder_grants_dir = self.root / "folder_grants"
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.managed_roots_dir.mkdir(parents=True, exist_ok=True)
        self.folder_grants_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.ensure_general()

    def _path(self, workspace_id: str) -> Path:
        if workspace_id != GENERAL_WORKSPACE_ID and not _WORKSPACE_ID_RE.fullmatch(
                workspace_id or ""):
            raise WorkspaceNotFound(f"研究空间不存在: {workspace_id}")
        return self.workspaces_dir / f"{workspace_id}.json"

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _managed_root(self, workspace_id: str) -> Path:
        base = self.managed_roots_dir.resolve()
        path = (base / workspace_id).resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:  # defense in depth; ids are already strict
            raise WorkspaceError("托管目录越出控制台数据范围") from exc
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _paths_overlap(first: Path, second: Path) -> bool:
        """Whether either resolved directory contains the other."""
        a = os.path.normcase(str(first))
        b = os.path.normcase(str(second))
        try:
            common = os.path.commonpath([a, b])
        except ValueError:
            return False  # different Windows drives cannot overlap
        return common == a or common == b

    def _assert_external_root_safe(
        self, selected: Path, *, excluding_workspace_id: Optional[str] = None,
    ) -> None:
        """Keep a coding root away from Guanlan's control/global state.

        A root that contains the application repository is equally unsafe as a
        root inside it: either shape would let an autonomous edit reach global
        factors, workflows, Console metadata or Luozi data.  Workspace roots
        may not nest either, so one space can never edit another through a
        relative path.
        """
        selected = selected.resolve(strict=True)
        repo_root = Path(__file__).resolve().parents[2]
        protected = [
            (self.root.resolve(), "观澜控制数据目录"),
            (repo_root.resolve(), "观澜程序目录"),
        ]
        # Deployment may keep global stores and market data outside the repo.
        # Resolve the actual configured roots and protect them just like the
        # built-in repository/control-plane directories.
        env_roots = {
            "GUANLAN_WORKFLOW_ROOT": "全局工作流库",
            "GUANLAN_WISDOM_ROOT": "全局经验卡库",
            "GUANLAN_ORCH_STORE_ROOT": "全局编排库",
            "FA_QLIB_URI": "全局行情数据",
            "FA_QLIB_ETF_URI": "全局 ETF 行情数据",
            "FA_PARQUET_ROOT": "全局行情数据",
            "FA_NEWS_DATA_ROOT": "全局新闻数据",
            "FA_PIT_STORE_ROOT": "全局 PIT 数据",
        }
        for env_name, label in env_roots.items():
            raw = str(os.environ.get(env_name) or "").strip()
            if raw:
                try:
                    protected.append((Path(raw).expanduser().resolve(), label))
                except (OSError, RuntimeError):
                    continue
        try:
            from financial_analyst.data.paths import get_data_paths
            data = get_data_paths()
            roots = [data.qlib_day, data.qlib_5min, data.qlib_etf,
                     data.parquet_root, data.news_data_root, data.pit_store_root]
            for raw in roots:
                if raw:
                    protected.append((Path(raw).expanduser().resolve(), "全局市场数据目录"))
        except Exception:
            pass
        for boundary, label in protected:
            if self._paths_overlap(selected, boundary):
                raise FolderGrantError(f"所选目录不能与{label}重叠")

        for item in self.list(include_archived=True):
            if item.get("id") == excluding_workspace_id:
                continue
            raw = str(item.get("root_path") or "").strip()
            if not raw:
                continue
            try:
                other = Path(raw).resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if self._paths_overlap(selected, other):
                raise FolderGrantError("所选目录不能与其他研究空间嵌套或重叠")

    def ensure_general(self) -> Dict[str, Any]:
        with self._lock:
            path = self.workspaces_dir / f"{GENERAL_WORKSPACE_ID}.json"
            if path.exists():
                item = self.get(GENERAL_WORKSPACE_ID)
                if item is not None:
                    return item
            now = _now()
            item = {
                "id": GENERAL_WORKSPACE_ID,
                "name": "通用研究",
                "kind": "general",
                "code": "",
                "display_name": "通用研究",
                "root_mode": "managed",
                "root_path": str(self._managed_root(GENERAL_WORKSPACE_ID)),
                "agent_mode": "research",
                "created": now,
                "updated": now,
                "archived": False,
            }
            self._write_json(path, item)
            return dict(item)

    def get(self, workspace_id: str, *, active_only: bool = False) -> Optional[Dict[str, Any]]:
        with self._lock:
            try:
                path = self._path(workspace_id)
            except WorkspaceNotFound:
                return None
            if not path.exists():
                return None
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
            if not isinstance(item, dict) or item.get("id") != workspace_id:
                return None
            if active_only and item.get("archived"):
                return None
            return item

    def require(self, workspace_id: str, *, active_only: bool = True) -> Dict[str, Any]:
        item = self.get(workspace_id, active_only=active_only)
        if item is None:
            raise WorkspaceNotFound(f"研究空间不存在或已归档: {workspace_id}")
        return item

    def list(self, *, include_archived: bool = True) -> List[Dict[str, Any]]:
        with self._lock:
            items: List[Dict[str, Any]] = []
            for path in self.workspaces_dir.glob("rw_*.json"):
                item = self.get(path.stem)
                if item is None or (item.get("archived") and not include_archived):
                    continue
                items.append(item)
            return sorted(
                items,
                key=lambda item: (
                    item.get("id") != GENERAL_WORKSPACE_ID,
                    item.get("archived", False),
                    str(item.get("updated", "")),
                ),
            )

    def _assert_unique_code(self, *, kind: str, code: str,
                            excluding: Optional[str] = None) -> None:
        if kind not in {"stock", "etf"} or not code:
            return
        for item in self.list(include_archived=False):
            # Seats assignments are keyed by canonical security code, not by
            # the UI's stock/ETF label.  Keep one active owner across both
            # kinds so the workspace projection can never select by list order.
            if (item.get("id") != excluding
                    and item.get("kind") in {"stock", "etf"}
                    and item.get("code") == code):
                raise WorkspaceError(f"{code} 已有一个未归档的研究空间")

    def find_active_security_workspace(self, code: Any) -> Optional[Dict[str, Any]]:
        """Resolve one canonical code to its sole active stock/ETF workspace.

        Cross-surface projections are keyed by symbol rather than by the UI's
        stock/ETF label.  Legacy or corrupt duplicates therefore fail closed
        instead of being selected by list order.
        """
        canonical = normalize_security_code(code)
        if not canonical:
            return None
        matches = [
            item for item in self.list(include_archived=False)
            if item.get("kind") in {"stock", "etf"}
            and item.get("code") == canonical
        ]
        if len(matches) > 1:
            raise WorkspaceAmbiguous(
                f"{canonical} 对应多个未归档研究空间，已拒绝歧义投影"
            )
        return dict(matches[0]) if matches else None

    def create(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            kind = str(payload.get("kind") or "stock").strip().lower()
            if kind not in WORKSPACE_KINDS or kind == "general":
                raise WorkspaceError("kind 仅支持 stock/etf；通用研究空间已内置")
            code = normalize_security_code(payload.get("code"))
            if not code:
                raise WorkspaceError("股票/ETF 研究空间必须填写证券代码")
            self._assert_unique_code(kind=kind, code=code)
            name = _clean_text(
                payload.get("name") or payload.get("display_name") or code,
                field="空间名称", limit=60, required=True,
            )
            display_name = _clean_text(
                payload.get("display_name") or name,
                field="显示名称", limit=60, required=True,
            )
            agent_mode = str(payload.get("agent_mode") or "collaborative").strip().lower()
            if agent_mode not in AGENT_MODES:
                raise WorkspaceError("agent_mode 仅支持 research/collaborative/autonomous")
            root_mode = str(payload.get("root_mode") or "managed").strip().lower()
            if root_mode not in ROOT_MODES:
                raise WorkspaceError("root_mode 仅支持 managed/external")
            if payload.get("root_path"):
                raise WorkspaceError("不能通过 HTTP 直接指定目录，请使用桌面文件夹选择授权")

            workspace_id = "rw_" + uuid.uuid4().hex[:12]
            if root_mode == "managed":
                root_path = self._managed_root(workspace_id)
            else:
                root_path = self.consume_folder_grant(
                    payload.get("folder_grant"), excluding_workspace_id=workspace_id,
                )
            now = _now()
            item = {
                "id": workspace_id,
                "name": name,
                "kind": kind,
                "code": code,
                "display_name": display_name,
                "root_mode": root_mode,
                "root_path": str(root_path),
                "agent_mode": agent_mode,
                "created": now,
                "updated": now,
                # The global Seats history is code-scoped.  Persist the period
                # during which this workspace owned that code so re-binding or
                # archiving cannot pull unrelated history into the Dock.
                "subject_since": now,
                "subject_until": None,
                "archived": False,
            }
            self._write_json(self._path(workspace_id), item)
            return dict(item)

    def update(self, workspace_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            item = self.require(workspace_id, active_only=True)
            changed = False
            if "name" in payload:
                item["name"] = _clean_text(
                    payload.get("name"), field="空间名称", limit=60, required=True,
                )
                changed = True
            if "display_name" in payload:
                item["display_name"] = _clean_text(
                    payload.get("display_name"), field="显示名称", limit=60, required=True,
                )
                changed = True
            if "agent_mode" in payload:
                mode = str(payload.get("agent_mode") or "").strip().lower()
                if mode not in AGENT_MODES:
                    raise WorkspaceError(
                        "agent_mode 仅支持 research/collaborative/autonomous"
                    )
                item["agent_mode"] = mode
                changed = True
            if "code" in payload:
                if item["kind"] == "general":
                    raise WorkspaceError("通用研究空间没有证券代码")
                code = normalize_security_code(payload.get("code"))
                if not code:
                    raise WorkspaceError("证券代码不能为空")
                self._assert_unique_code(
                    kind=str(item["kind"]), code=code, excluding=workspace_id,
                )
                if code != item.get("code"):
                    item["code"] = code
                    item["subject_since"] = _now()
                    item["subject_until"] = None
                    changed = True
            if "root_path" in payload:
                raise WorkspaceError("不能通过 HTTP 直接指定目录，请使用桌面文件夹选择授权")
            if "root_mode" in payload:
                root_mode = str(payload.get("root_mode") or "").strip().lower()
                if root_mode not in ROOT_MODES:
                    raise WorkspaceError("root_mode 仅支持 managed/external")
                if root_mode == "external":
                    item["root_path"] = str(
                        self.consume_folder_grant(
                            payload.get("folder_grant"),
                            excluding_workspace_id=workspace_id,
                        )
                    )
                elif "folder_grant" in payload:
                    raise WorkspaceError("folder_grant 只能用于 root_mode=external")
                elif item.get("root_mode") != "managed":
                    item["root_path"] = str(self._managed_root(workspace_id))
                item["root_mode"] = root_mode
                changed = True
            elif "folder_grant" in payload:
                # A grant without an explicit external transition is ambiguous;
                # fail closed instead of silently rebinding a directory.
                raise WorkspaceError("folder_grant 必须与 root_mode=external 一起提交")
            if not changed:
                raise WorkspaceError("无可更新字段")
            item["updated"] = _now()
            self._write_json(self._path(workspace_id), item)
            return dict(item)

    def archive(self, workspace_id: str) -> Dict[str, Any]:
        with self._lock:
            if workspace_id == GENERAL_WORKSPACE_ID:
                raise WorkspaceError("通用研究空间不能归档")
            item = self.require(workspace_id, active_only=False)
            if not item.get("archived"):
                now = _now()
                item["archived"] = True
                item["subject_until"] = now
                item["updated"] = now
                self._write_json(self._path(workspace_id), item)
            # Deliberately do not touch root_path.  It may be an external folder
            # owned by the user, and managed roots are also retained for restore.
            return dict(item)

    def issue_folder_grant(self, directory: Any, *, ttl_seconds: int = 300) -> Dict[str, Any]:
        """Create the grant consumed by the HTTP workspace endpoint.

        This method is intended for the trusted desktop bridge after a native
        folder-selection dialog.  The browser never supplies ``directory``.
        """
        with self._lock:
            if ttl_seconds < 1 or ttl_seconds > 3600:
                raise FolderGrantError("目录授权有效期不合法")
            raw = Path(str(directory or "")).expanduser()
            if not raw.is_absolute():
                raise FolderGrantError("请选择绝对目录")
            try:
                path = raw.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise FolderGrantError("所选目录不存在或不可访问") from exc
            if not path.is_dir():
                raise FolderGrantError("所选路径不是目录")
            self._assert_external_root_safe(path)
            grant_id = "fg_" + uuid.uuid4().hex[:24]
            item = {
                "id": grant_id,
                "path": str(path),
                "created": _now(),
                "expires_at": time.time() + ttl_seconds,
            }
            self._write_json(self.folder_grants_dir / f"{grant_id}.json", item)
            return {"id": grant_id, "path": str(path), "expires_at": item["expires_at"]}

    def consume_folder_grant(
        self, grant_id: Any, *, excluding_workspace_id: Optional[str] = None,
    ) -> Path:
        with self._lock:
            grant_id = str(grant_id or "").strip()
            if not _FOLDER_GRANT_RE.fullmatch(grant_id):
                raise FolderGrantError("缺少有效的桌面目录授权")
            path = self.folder_grants_dir / f"{grant_id}.json"
            if not path.exists():
                raise FolderGrantError("目录授权不存在或已使用")
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                path.unlink(missing_ok=True)
                raise FolderGrantError("目录授权已损坏") from exc
            # Unlink before further checks: grants are one-shot even when stale
            # or the selected folder has disappeared.
            path.unlink(missing_ok=True)
            if item.get("id") != grant_id or float(item.get("expires_at", 0)) < time.time():
                raise FolderGrantError("目录授权已过期")
            try:
                selected = Path(str(item.get("path") or "")).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise FolderGrantError("授权目录不存在或不可访问") from exc
            if not selected.is_dir() or not selected.is_absolute():
                raise FolderGrantError("授权路径不是目录")
            # Re-check at consumption: another workspace may have claimed an
            # overlapping root during the short grant lifetime.
            self._assert_external_root_safe(
                selected, excluding_workspace_id=excluding_workspace_id,
            )
            return selected
