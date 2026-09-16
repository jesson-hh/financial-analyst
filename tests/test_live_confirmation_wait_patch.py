"""Isolated regression for yielded Codex approval cells; never write live."""
import ast
import asyncio
import hashlib
import importlib.util
import json
import types
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "var/agent_workflow_20260905/live_confirmation_wait.patch"
pytestmark = [
    pytest.mark.realdata,
    pytest.mark.skipif(not PATCH.is_file(), reason="private live patch fixture is not distributed"),
]


def sources():
    # Reuse the exact-context ordered in-memory patch verifier, not app code.
    path = ROOT / "tests/test_live_workflow_codex_patch.py"
    spec = importlib.util.spec_from_file_location("approval_patch_helpers", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.PATCH = PATCH
    return helper.patched_sources()


def callback_fixture(timeout=0.2):
    text = sources()[0]["G:/guanlan-v2/guanlan_v2/console/api.py"]
    node = next(n for n in ast.walk(ast.parse(text))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_confirm_once")
    events, control, pending, metadata = [], {}, {}, {}
    visible = asyncio.Event()
    async def extras(*args, **kwargs):
        return {}
    def emit(sid, kind, **payload):
        events.append({"kind": kind, **payload})
        if kind == "confirm_request":
            visible.set()
    scope = {"asyncio": asyncio, "uuid": uuid, "sid": "cs_111111111111",
             "turn_id": "test-turn", "workspace": {},
             "channel_agent_tools": types.SimpleNamespace(CHANNEL_CONFIRM_TOOL_NAMES=set()),
             "wt": types.SimpleNamespace(may_auto_approve=lambda *args: False),
             "control": control, "pending": pending, "pending_confirm_meta": metadata,
             "_emit": emit, "_safe": lambda x: x, "_confirm_extras": extras,
             "_public_confirm_args": lambda name, args: args, "_CONFIRM_TIMEOUT": timeout}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "actual-confirm-callback", "exec"), scope)
    return scope["_confirm_once"], visible, events, control, pending, metadata


def test_live_patch_exact_unique_contexts_and_syntax():
    _, manifest = sources()
    assert len(manifest) == 2
    for path, row in manifest.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == row["old_sha256"]


def test_cancelled_scope_publishes_cancel_and_never_approves():
    async def run():
        fn, visible, events, control, pending, metadata = callback_fixture()
        task = asyncio.create_task(fn("ww_workflow_save", {"name": "same graph"}))
        await asyncio.wait_for(visible.wait(), 0.2)
        confirm_id = control["pending_confirm"]["confirm_id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        resolved = [e for e in events if e["kind"] == "confirm_resolved"]
        assert len(resolved) == 1
        assert resolved[0]["confirm_id"] == confirm_id and resolved[0]["choice"] == "cancelled"
        assert "未执行" in resolved[0]["reason"]
        assert not pending and not metadata and control["pending_confirm"] is None
    asyncio.run(run())


def test_user_cancel_or_steer_does_not_duplicate_resolution():
    async def run():
        fn, visible, events, control, pending, metadata = callback_fixture()
        task = asyncio.create_task(fn("ww_workflow_run", {"workflow_id": "w1234567890ab"}))
        await asyncio.wait_for(visible.wait(), 0.2)
        # Mirror the existing Console cancellation path: it resolves the card
        # before cancelling the coroutine. The new handler must not emit twice.
        confirm_id = control["pending_confirm"]["confirm_id"]
        events.append({"kind": "confirm_resolved", "confirm_id": confirm_id, "choice": "steer"})
        control["pending_confirm"] = None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [e["choice"] for e in events if e["kind"] == "confirm_resolved"] == ["steer"]
        assert not pending and not metadata
    asyncio.run(run())


def test_confirmation_still_waits_for_actual_approval():
    async def run():
        fn, visible, events, control, pending, _ = callback_fixture()
        task = asyncio.create_task(fn("ww_workflow_save", {"name": "same graph"}))
        await asyncio.wait_for(visible.wait(), 0.2)
        assert not task.done()
        pending["test-turn"][1].set_result("y")
        assert await task is True
        assert not any(e.get("choice") == "cancelled" for e in events)
        assert control["phase"] == "tool"
    asyncio.run(run())


def test_timeout_still_denies_and_reports_timeout():
    async def run():
        fn, _, events, control, pending, _ = callback_fixture(timeout=0.01)
        assert await fn("ww_workflow_run", {}) is False
        assert [e["choice"] for e in events if e["kind"] == "confirm_resolved"] == ["timeout"]
        assert not pending and control["pending_confirm"] is None
    asyncio.run(run())


def test_prompt_waits_on_same_cell_without_resubmitting_or_finalizing():
    text = sources()[0]["G:/guanlan-v2/guanlan_v2/console/codex_backend.py"]
    for phrase in ("持续调用 functions.wait", "yield_time_ms=30000", "不得重复提交原工具",
                   "未完成前绝不能输出 final", "结束会撤销确认卡", "拒绝、取消或超时就不得执行"):
        assert phrase in text
    # Scope teardown and permission checks are intentionally unchanged.
    assert "revoke_codex_scope(scope_token)" in text


if __name__ == "__main__":
    _, manifest = sources()
    output = PATCH.with_name("live_confirmation_wait_hashes.json")
    output.write_text(json.dumps({"patch_sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest(),
        "files": manifest, "live_files_written": False,
        "verified": "exact contexts + AST + isolated callback tests"}, indent=2), encoding="utf-8")
    print(output)
