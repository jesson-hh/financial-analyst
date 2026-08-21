"""ResearchWorkspace code-agent capability and isolation contracts."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sys

import pytest

from guanlan_v2.console.store import ConsoleStore
from guanlan_v2.console import workspace_tools as wt


@contextmanager
def _bound(store: ConsoleStore, sid: str):
    token = wt.CTX_WORKSPACE.set(wt.bind_workspace_context(store, sid))
    try:
        yield
    finally:
        wt.CTX_WORKSPACE.reset(token)


def _space(store: ConsoleStore, code: str, *, mode: str = "collaborative"):
    ws = store.workspace_store.create({
        "name": code, "kind": "stock", "code": code, "agent_mode": mode,
    })
    sid = store.create_session("研究", workspace_id=ws["id"])["id"]
    return ws, sid


@contextmanager
def _console_bound(store: ConsoleStore, sid: str):
    from guanlan_v2.console import tools as ct
    workspace_token = wt.CTX_WORKSPACE.set(wt.bind_workspace_context(store, sid))
    store_token = ct.CTX_STORE.set(store)
    sid_token = ct.CTX_SID.set(sid)
    try:
        yield ct
    finally:
        ct.CTX_SID.reset(sid_token)
        ct.CTX_STORE.reset(store_token)
        wt.CTX_WORKSPACE.reset(workspace_token)


def test_workspace_read_search_and_secret_boundary(tmp_path):
    store = ConsoleStore(root=tmp_path)
    ws, sid = _space(store, "600519")
    root = tmp_path / "workspace_files" / ws["id"]
    (root / "src").mkdir()
    (root / "src" / "alpha.py").write_text("signal = 'quality'\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=never", encoding="utf-8")

    with _bound(store, sid):
        tree = wt.workspace_tree_impl(depth=3)
        assert tree["ok"] is True
        assert "src/alpha.py" in tree["content"] and ".env" not in tree["content"]
        read = wt.workspace_read_impl("src/alpha.py")
        assert read["ok"] is True and len(read["sha256"]) == 64
        found = wt.workspace_search_impl("quality")
        assert found["matches"][0]["path"] == "src/alpha.py"
        assert wt.workspace_read_impl(".env")["ok"] is False
        assert wt.workspace_read_impl("../outside.txt")["ok"] is False
        assert wt.workspace_read_impl(str((tmp_path / "outside.txt").resolve()))["ok"] is False


def test_workspace_symlink_escape_is_not_visible(tmp_path):
    store = ConsoleStore(root=tmp_path)
    ws, sid = _space(store, "300750")
    root = tmp_path / "workspace_files" / ws["id"]
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    link = root / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("当前 Windows 权限不允许创建符号链接")

    with _bound(store, sid):
        assert wt.workspace_read_impl("outside-link.txt")["ok"] is False
        assert "outside-link.txt" not in wt.workspace_tree_impl()["content"]


def test_patch_is_hash_guarded_journaled_and_reversible(tmp_path):
    store = ConsoleStore(root=tmp_path)
    ws, sid = _space(store, "000001")
    path = tmp_path / "workspace_files" / ws["id"] / "model.py"
    path.write_text("score = 1\n", encoding="utf-8")

    with _bound(store, sid):
        current = wt.workspace_read_impl("model.py")
        stale = wt.workspace_apply_patch_impl(
            "model.py", "score = 1", "score = 2", expected_sha256="0" * 64,
        )
        assert stale["ok"] is False and path.read_text(encoding="utf-8") == "score = 1\n"

        changed = wt.workspace_apply_patch_impl(
            "model.py", "score = 1", "score = 2",
            expected_sha256=current["sha256"], reason="raise score",
        )
        assert changed["ok"] is True and path.read_text(encoding="utf-8") == "score = 2\n"
        assert wt.list_workspace_runs(store, ws["id"])[0]["changed_files"] == ["model.py"]

        undone = wt.workspace_undo_impl(changed["change_id"])
        assert undone["ok"] is True and path.read_text(encoding="utf-8") == "score = 1\n"

        reread = wt.workspace_read_impl("model.py")
        second = wt.workspace_apply_patch_impl(
            "model.py", "score = 1", "score = 3", expected_sha256=reread["sha256"],
        )
        path.write_text("score = 4\n", encoding="utf-8")
        refused = wt.workspace_undo_impl(second["change_id"])
        assert refused["ok"] is False and path.read_text(encoding="utf-8") == "score = 4\n"


def test_modes_and_global_write_boundary_are_fail_closed(tmp_path):
    store = ConsoleStore(root=tmp_path)
    read_only, read_sid = _space(store, "601318", mode="research")
    autonomous, auto_sid = _space(store, "000858", mode="autonomous")
    base = set(wt.ALL_WORKSPACE_TOOLS) | {"ww_factorlib_save", "ww_research_loop", "ww_quote"}

    allowed_read = wt.allowed_tools_for_workspace(base, read_only)
    assert wt.READ_TOOLS <= allowed_read
    assert not (wt.MUTATING_TOOLS & allowed_read)
    assert "ww_factorlib_save" not in allowed_read
    assert "ww_research_loop" not in allowed_read
    assert "ww_quote" in allowed_read

    allowed_auto = wt.allowed_tools_for_workspace(base, autonomous)
    assert wt.ALL_WORKSPACE_TOOLS <= allowed_auto
    assert not wt.may_auto_approve(autonomous, "ww_workspace_factor_publish", {})
    assert not wt.may_auto_approve(autonomous, "ww_workspace_run", {"task": "pytest"})
    assert wt.may_auto_approve(autonomous, "ww_workspace_apply_patch", {})
    assert not wt.may_auto_approve(autonomous, "ww_workspace_run", {"task": "compile_python"})

    with _bound(store, read_sid):
        denied = wt.workspace_apply_patch_impl("new.py", new_text="x = 1\n", create=True)
        assert denied["ok"] is False
    with _bound(store, auto_sid):
        created = wt.workspace_apply_patch_impl("new.py", new_text="x = 1\n", create=True)
        assert created["ok"] is True
        checked = wt.workspace_run_impl("compile_python", ["new.py"])
        assert checked["ok"] is True


def test_bound_workspace_cannot_read_another_workspace(tmp_path):
    store = ConsoleStore(root=tmp_path)
    first, first_sid = _space(store, "600036")
    second, _second_sid = _space(store, "600276")
    first_root = tmp_path / "workspace_files" / first["id"]
    second_root = tmp_path / "workspace_files" / second["id"]
    (first_root / "own.txt").write_text("own", encoding="utf-8")
    (second_root / "other.txt").write_text("other", encoding="utf-8")

    with _bound(store, first_sid):
        assert wt.workspace_read_impl("own.txt")["ok"] is True
        # The model cannot provide a workspace id or root; traversal/absolute
        # forms are rejected before the other root is touched.
        assert wt.workspace_read_impl(f"../../workspace_files/{second['id']}/other.txt")["ok"] is False
        assert wt.workspace_read_impl(str(second_root / "other.txt"))["ok"] is False


def test_windows_ads_and_device_names_are_rejected(tmp_path):
    store = ConsoleStore(root=tmp_path)
    _ws, sid = _space(store, "600000")
    with _bound(store, sid):
        for unsafe in ("safe.txt::$DATA", "CON", "aux.py", "dir/NUL.txt"):
            out = wt.workspace_apply_patch_impl(
                unsafe, new_text="no\n", create=True,
            )
            assert out["ok"] is False, unsafe


def test_apply_and_undo_recover_from_journal_finalize_failures(tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path)
    ws, sid = _space(store, "600010")
    target = tmp_path / "workspace_files" / ws["id"] / "alpha.py"
    target.write_text("value = 1\n", encoding="utf-8")
    original_atomic = wt._atomic_json

    with _bound(store, sid):
        digest = wt.workspace_read_impl("alpha.py")["sha256"]

        def fail_applied(path, payload):
            if payload.get("state") == "applied":
                raise OSError("simulated journal finalize failure")
            return original_atomic(path, payload)

        monkeypatch.setattr(wt, "_atomic_json", fail_applied)
        interrupted = wt.workspace_apply_patch_impl(
            "alpha.py", "value = 1", "value = 2", expected_sha256=digest,
        )
        assert interrupted["ok"] is False
        assert target.read_text(encoding="utf-8") == "value = 2\n"
        change_id = interrupted["change_id"]

        monkeypatch.setattr(wt, "_atomic_json", original_atomic)
        recovered = wt.workspace_undo_impl(change_id)
        assert recovered["ok"] is True
        assert target.read_text(encoding="utf-8") == "value = 1\n"

        digest = wt.workspace_read_impl("alpha.py")["sha256"]
        changed = wt.workspace_apply_patch_impl(
            "alpha.py", "value = 1", "value = 3", expected_sha256=digest,
        )

        def fail_undone(path, payload):
            if payload.get("state") == "undone":
                raise OSError("simulated undo finalize failure")
            return original_atomic(path, payload)

        monkeypatch.setattr(wt, "_atomic_json", fail_undone)
        interrupted_undo = wt.workspace_undo_impl(changed["change_id"])
        assert interrupted_undo["ok"] is False
        assert target.read_text(encoding="utf-8") == "value = 1\n"
        monkeypatch.setattr(wt, "_atomic_json", original_atomic)
        assert wt.workspace_undo_impl(changed["change_id"])["ok"] is True


def test_failed_file_replace_is_reconciled_as_aborted_and_retryable(tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path)
    ws, sid = _space(store, "600011")
    target = tmp_path / "workspace_files" / ws["id"] / "alpha.py"
    target.write_text("value = 1\n", encoding="utf-8")
    real_replace = wt.os.replace

    with _bound(store, sid):
        digest = wt.workspace_read_impl("alpha.py")["sha256"]
        def fail_target_replace(src, dst):
            if str(dst) == str(target):
                raise OSError("replace failed")
            return real_replace(src, dst)

        monkeypatch.setattr(wt.os, "replace", fail_target_replace)
        failed = wt.workspace_apply_patch_impl(
            "alpha.py", "value = 1", "value = 2", expected_sha256=digest,
        )
        assert failed["ok"] is False
        assert target.read_text(encoding="utf-8") == "value = 1\n"
        journal = json.loads((tmp_path / "workspace_changes" / ws["id"] / f"{failed['change_id']}.json").read_text(encoding="utf-8"))
        assert journal["state"] == "aborted"
        monkeypatch.setattr(wt.os, "replace", real_replace)
        retry = wt.workspace_apply_patch_impl(
            "alpha.py", "value = 1", "value = 2", expected_sha256=digest,
        )
        assert retry["ok"] is True


def test_failed_undo_replace_resumes_from_undoing_state(tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path)
    ws, sid = _space(store, "600012")
    target = tmp_path / "workspace_files" / ws["id"] / "alpha.py"
    target.write_text("value = 1\n", encoding="utf-8")
    real_replace = wt.os.replace

    with _bound(store, sid):
        digest = wt.workspace_read_impl("alpha.py")["sha256"]
        changed = wt.workspace_apply_patch_impl(
            "alpha.py", "value = 1", "value = 2", expected_sha256=digest,
        )

        def fail_target_replace(src, dst):
            if str(dst) == str(target):
                raise OSError("undo replace failed")
            return real_replace(src, dst)

        monkeypatch.setattr(wt.os, "replace", fail_target_replace)
        failed = wt.workspace_undo_impl(changed["change_id"])
        assert failed["ok"] is False
        assert target.read_text(encoding="utf-8") == "value = 2\n"
        monkeypatch.setattr(wt.os, "replace", real_replace)
        resumed = wt.workspace_undo_impl(changed["change_id"])
        assert resumed["ok"] is True
        assert target.read_text(encoding="utf-8") == "value = 1\n"


def test_run_output_is_bounded_and_psutil_is_required(tmp_path, monkeypatch):
    code = "import sys; sys.stdout.write('x' * (2 * 1024 * 1024))"
    rc, output, _elapsed = wt._run_command([sys.executable, "-I", "-c", code], tmp_path, 10)
    assert rc == 125
    assert "输出超过 1MB" in output
    assert len(output.encode("utf-8")) <= wt._MAX_PROCESS_OUTPUT_BYTES + 200

    monkeypatch.setitem(sys.modules, "psutil", None)
    with pytest.raises(wt.WorkspaceToolError, match="psutil"):
        wt._run_command([sys.executable, "-I", "-c", "print('never')"], tmp_path, 10)


def test_workspace_memory_is_shared_within_space_and_isolated_across_spaces(tmp_path):
    from guanlan_v2.console.api import _memory_block

    store = ConsoleStore(root=tmp_path)
    first, first_sid = _space(store, "600519")
    peer_sid = store.create_session("同票第二会话", workspace_id=first["id"])["id"]
    _other, other_sid = _space(store, "000858")

    with _console_bound(store, first_sid) as ct:
        saved = ct.memory_write_impl(
            "护城河假设需跟踪毛利率验证", scope="workspace", key="核心假设",
        )
        assert saved["ok"] is True

    with _console_bound(store, peer_sid) as ct:
        recalled = ct.memory_read_impl("workspace")
        assert "护城河假设" in recalled["content"]
        assert "护城河假设" in _memory_block(peer_sid, first["id"])

    with _console_bound(store, other_sid) as ct:
        isolated = ct.memory_read_impl("workspace")
        assert "护城河假设" not in isolated["content"]
        assert "护城河假设" not in _memory_block(other_sid, _other["id"])


def test_public_asset_index_aggregates_only_this_workspace(tmp_path, monkeypatch):
    import guanlan_v2.reports.api as reports_api
    from guanlan_v2.workflow.store import WorkflowStore

    monkeypatch.setenv("GUANLAN_WORKFLOW_ROOT", str(tmp_path / "workflows"))
    monkeypatch.setattr(reports_api, "_STORE", tmp_path / "reports")
    store = ConsoleStore(root=tmp_path / "console")
    first, _sid = _space(store, "300750")
    second, _sid2 = _space(store, "600036")

    factor = {
        "id": "wa_1111111111111111", "workspace_id": first["id"],
        "scope": "workspace", "kind": "factor", "name": "质量动量",
        "status": "draft", "version": 1, "updated": "2026-08-13T10:00:00",
        "summary": "subject · rank(close)", "open_page": "factor",
        "ref": "wa_1111111111111111",
    }
    wt._atomic_json(wt._asset_path(store, first["id"], factor["id"]), factor)
    WorkflowStore().save("个股验证链", {"nodes": [{"id": "n1"}], "edges": []},
                         workspace_id=first["id"])
    WorkflowStore().save("别票工作流", {"nodes": [], "edges": []},
                         workspace_id=second["id"])
    reports_api._STORE.mkdir(parents=True)
    (reports_api._STORE / "r1.json").write_text(json.dumps({
        "id": "r1", "name": "验证报告", "ts": "2026-08-13T11:00:00",
        "scope": "workspace", "workspace_id": first["id"],
        "result": {}, "graph": None, "kpi": {},
    }), encoding="utf-8")
    (reports_api._STORE / "r2.json").write_text(json.dumps({
        "id": "r2", "name": "别票报告", "ts": "2026-08-13T12:00:00",
        "scope": "workspace", "workspace_id": second["id"],
        "result": {}, "graph": None, "kpi": {},
    }), encoding="utf-8")

    rows = wt.public_asset_rows(store, first["id"])
    assert {row["kind"] for row in rows} == {"factor", "workflow", "report"}
    assert {row["name"] for row in rows} == {"质量动量", "个股验证链", "验证报告"}
    workflow = next(row for row in rows if row["kind"] == "workflow")
    assert workflow["open_page"] == "factor" and workflow["ref"]


def test_factor_publish_recovers_after_global_commit_before_asset_finalize(tmp_path, monkeypatch):
    import guanlan_v2.factorlib.store as factor_store_module

    store = ConsoleStore(root=tmp_path / "console")
    workspace, sid = _space(store, "510300")
    asset = {
        "id": "wa_2222222222222222", "workspace_id": workspace["id"],
        "scope": "workspace", "kind": "factor", "name": "恢复因子",
        "status": "draft", "version": 1, "expr": "rank(close)",
        "description": "幂等发布测试", "applicability": "subject",
        "subject": {"kind": "stock", "code": "510300", "name": "510300"},
        "created": "2026-08-13T10:00:00", "updated": "2026-08-13T10:00:00",
        "published": None,
    }
    asset_path = wt._asset_path(store, workspace["id"], asset["id"])
    wt._atomic_json(asset_path, asset)
    factor_store = factor_store_module.LibraryFactorStore(
        base_dir=tmp_path / "factor_base", mined_dir=tmp_path / "factor_mined",
    )
    monkeypatch.setattr(factor_store_module, "LibraryFactorStore", lambda: factor_store)
    original_atomic = wt._atomic_json

    def fail_asset_finalize(path, payload):
        if path == asset_path and payload.get("published"):
            raise OSError("simulated asset finalize failure")
        return original_atomic(path, payload)

    with _bound(store, sid):
        monkeypatch.setattr(wt, "_atomic_json", fail_asset_finalize)
        first = wt.workspace_factor_publish_impl(asset["id"], "ws_factor_recovery")
        assert first["ok"] is False
        assert len(factor_store.load_all()) == 1
        pending = json.loads(asset_path.read_text(encoding="utf-8"))
        assert pending["publish_pending"]["global_name"] == "ws_factor_recovery"

        monkeypatch.setattr(wt, "_atomic_json", original_atomic)
        recovered = wt.workspace_factor_publish_impl(asset["id"], "different_name")
        assert recovered["ok"] is True

    final = json.loads(asset_path.read_text(encoding="utf-8"))
    assert final["published"]["global_name"] == "ws_factor_recovery"
    assert final["published"]["recovered"] is True
    assert "publish_pending" not in final
    assert len(factor_store.load_all()) == 1
