"""Short-lived, process-local capability scopes for the Weiwo Codex bridge.

The public ``/gl-mcp`` transport keeps its existing behaviour.  The dedicated
``/gl-mcp-codex`` transport requires an unguessable header registered here for
the lifetime of one Codex App Server child.  The scope is deliberately not
persisted: a server restart or a finished turn revokes it automatically.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import secrets
import threading
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, FrozenSet, Mapping, Optional


ConfirmCallback = Callable[[str, dict[str, Any]], Awaitable[bool]]
CompanyResearchLaunchCallback = Callable[
    [dict[str, Any]], Awaitable[Mapping[str, Any]]
]


@dataclass(frozen=True)
class CodexMcpScope:
    sid: str
    workspace_id: str
    workspace_code: str
    workspace_subject_sha256: str
    allowed_tools: FrozenSet[str]
    workspace_root_sha256: str = ""
    confirmation_required_tools: FrozenSet[str] = frozenset()
    confirm_callback: Optional[ConfirmCallback] = field(
        default=None, repr=False, compare=False,
    )
    company_research_launch_callback: Optional[CompanyResearchLaunchCallback] = field(
        default=None, repr=False, compare=False,
    )
    confirmation_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, repr=False, compare=False,
    )
    revoked: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False,
    )


_SCOPES: dict[str, CodexMcpScope] = {}
_LOCK = threading.Lock()
CTX_CODEX_MCP_SCOPE: contextvars.ContextVar[Optional[CodexMcpScope]] = (
    contextvars.ContextVar("guanlan_codex_mcp_scope", default=None)
)


def workspace_subject_sha256(workspace: Mapping[str, Any]) -> str:
    """Stable binding for the workspace's current researched security."""
    subject = {
        "id": str(workspace.get("id") or ""),
        "code": str(workspace.get("code") or ""),
        "subject_since": str(workspace.get("subject_since") or ""),
    }
    encoded = json.dumps(
        subject, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def workspace_root_sha256(root: Path | str) -> str:
    """Hash the trusted resolved workspace root using the backend's exact rule."""
    normalized = os.path.normcase(str(Path(root).resolve())).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def register_codex_scope(*, sid: str, workspace_id: str, workspace_code: str,
                         workspace_subject_sha256: str,
                         allowed_tools: FrozenSet[str],
                         workspace_root_sha256: str = "",
                         confirmation_required_tools: FrozenSet[str] = frozenset(),
                         confirm_callback: Optional[ConfirmCallback] = None,
                         company_research_launch_callback:
                         Optional[CompanyResearchLaunchCallback] = None) -> str:
    allowed = frozenset(str(name) for name in allowed_tools)
    confirmation_required = frozenset(
        str(name) for name in confirmation_required_tools
    )
    if not confirmation_required <= allowed:
        raise ValueError("Codex confirmation tools must be a subset of allowed tools")
    token = secrets.token_urlsafe(32)
    scope = CodexMcpScope(
        sid=str(sid),
        workspace_id=str(workspace_id),
        workspace_code=str(workspace_code),
        workspace_subject_sha256=str(workspace_subject_sha256),
        workspace_root_sha256=str(workspace_root_sha256),
        allowed_tools=allowed,
        confirmation_required_tools=confirmation_required,
        confirm_callback=confirm_callback,
        company_research_launch_callback=company_research_launch_callback,
    )
    with _LOCK:
        _SCOPES[token] = scope
    return token


def resolve_codex_scope(token: str) -> Optional[CodexMcpScope]:
    with _LOCK:
        scope = _SCOPES.get(str(token or ""))
        if scope is None or scope.revoked.is_set():
            return None
        return scope


def codex_scope_is_active(scope: CodexMcpScope) -> bool:
    """Check both the revoke signal and registry identity for an in-flight call."""
    if scope.revoked.is_set():
        return False
    with _LOCK:
        return any(candidate is scope for candidate in _SCOPES.values()) \
            and not scope.revoked.is_set()


async def _wait_until_revoked(scope: CodexMcpScope) -> None:
    # ``revoke_codex_scope`` can be reached from cancellation/finally code that
    # is not guaranteed to run in the MCP request task.  A thread-safe Event plus
    # short async polling avoids binding one Event/Future to the wrong loop.
    while not scope.revoked.is_set():
        await asyncio.sleep(0.05)


async def confirm_codex_tool_call(
    scope: CodexMcpScope,
    name: str,
    arguments: Mapping[str, Any],
) -> bool:
    """Run the Console confirmation exactly once, racing turn revocation.

    The callback receives a deep copy of the already server-normalised
    arguments.  The caller retains its own frozen execution copy, so UI/advisory
    code cannot change what will execute after approval.
    """
    if (
        name not in scope.confirmation_required_tools
        or not callable(scope.confirm_callback)
        or not codex_scope_is_active(scope)
    ):
        return False
    async with scope.confirmation_lock:
        if not codex_scope_is_active(scope):
            return False
        callback_task = asyncio.create_task(
            scope.confirm_callback(str(name), deepcopy(dict(arguments)))
        )
        revoked_task = asyncio.create_task(_wait_until_revoked(scope))
        try:
            done, _pending = await asyncio.wait(
                {callback_task, revoked_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if revoked_task in done:
                callback_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await callback_task
                return False
            revoked_task.cancel()
            with suppress(asyncio.CancelledError):
                await revoked_task
            try:
                approved = bool(await callback_task)
            except (asyncio.CancelledError, Exception):
                return False
            return approved and codex_scope_is_active(scope)
        finally:
            if not callback_task.done():
                callback_task.cancel()
            if not revoked_task.done():
                revoked_task.cancel()


def revoke_codex_scope(token: str) -> None:
    with _LOCK:
        scope = _SCOPES.pop(str(token or ""), None)
        if scope is not None:
            # Signal before releasing the registry lock so an already-running
            # MCP request cannot observe a post-pop but pre-signal window.
            scope.revoked.set()
