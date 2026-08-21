# -*- coding: utf-8 -*-
"""Native research-folder selection and one-time grant wiring."""
from __future__ import annotations

import json
import time
import types

from guanlan_v2.desktop import bridge as br
from guanlan_v2.desktop import shell as sh


def _api(*, chooser, factory):
    return br.JsApi(
        open_window_factory=lambda url: None,
        status_provider=lambda: {"state": "healthy"},
        retry_handler=lambda: None,
        log_opener=lambda: None,
        folder_chooser=chooser,
        folder_grant_factory=factory,
    )


def test_selected_folder_creates_canonical_workspace_store_grant(tmp_path):
    selected = tmp_path / "个股研究空间"
    selected.mkdir()
    console_root = tmp_path / "console"
    api = _api(
        chooser=lambda: [str(selected)],
        factory=lambda path: br.issue_research_folder_grant(
            path, console_root=console_root,
        ),
    )

    out = api.choose_research_folder()

    assert out["ok"] is True
    assert out["cancelled"] is False
    assert out["grant"].startswith("fg_")
    assert out["display_path"] == str(selected.resolve())
    assert out["path"] == str(selected.resolve())
    grants = console_root / "folder_grants"
    grant_files = list(grants.glob("fg_*.json"))
    assert grant_files == [grants / f"{out['grant']}.json"]
    payload = json.loads(grant_files[0].read_text(encoding="utf-8"))
    assert payload["id"] == out["grant"]
    assert payload["path"] == str(selected.resolve())
    assert 0 < payload["expires_at"] - time.time() <= 300
    assert payload["created"]
    assert not list(grants.glob("*.tmp")), "atomic writer must not leave partial grants"


def test_cancel_is_explicit_and_never_mints_a_grant():
    called = []
    api = _api(chooser=lambda: None, factory=lambda path: called.append(path))

    out = api.choose_research_folder()

    assert out["ok"] is False
    assert out["cancelled"] is True
    assert out["reason"] == "cancelled"
    assert called == []


def test_invalid_selected_file_does_not_create_authority(tmp_path):
    selected_file = tmp_path / "not-a-folder.txt"
    selected_file.write_text("x", encoding="utf-8")
    api = _api(
        chooser=lambda: str(selected_file),
        factory=lambda path: br.issue_research_folder_grant(
            path, console_root=tmp_path / "console",
        ),
    )

    out = api.choose_research_folder()

    assert out["ok"] is False
    assert out["cancelled"] is False
    assert out["reason"] == "folder-grant-failed"
    assert not list((tmp_path / "console" / "folder_grants").glob("fg_*.json"))


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class _DialogWindow:
    def __init__(self, title, url, **kwargs):
        self.title = title
        self.url = url
        self.kwargs = kwargs
        self.events = types.SimpleNamespace(loaded=_Event(), closed=_Event())
        self.dialog_calls = []

    def create_file_dialog(self, **kwargs):
        self.dialog_calls.append(kwargs)
        return [r"G:\research\510300"]

    def evaluate_js(self, code):
        return None


class _DialogWebview:
    FileDialog = types.SimpleNamespace(FOLDER=20)

    def __init__(self):
        self.windows = []

    def create_window(self, title, url=None, **kwargs):
        win = _DialogWindow(title, url, **kwargs)
        self.windows.append(win)
        return win

    def start(self, *args, **kwargs):
        return None


def test_shell_uses_pywebview_folder_dialog_and_injected_grant_factory():
    webview = _DialogWebview()
    received = []

    def issue(path):
        received.append(path)
        return {"id": "fg_" + "c" * 24, "path": path, "expires_at": time.time() + 300}

    shell = sh.create_shell(webview_module=webview, folder_grant_factory=issue)
    out = shell.api.choose_research_folder()

    assert webview.windows[0].dialog_calls == [{"dialog_type": webview.FileDialog.FOLDER}]
    assert received == [r"G:\research\510300"]
    assert out["grant"] == "fg_" + "c" * 24
    assert out["path"] == r"G:\research\510300"


def test_unbound_window_returns_safe_json_error():
    webview = _DialogWebview()
    shell = sh.create_shell(
        webview_module=webview,
        folder_grant_factory=lambda path: {"id": "fg_" + "d" * 24, "path": path},
    )
    shell.main_window = None

    out = shell.api.choose_research_folder()

    assert out["ok"] is False
    assert out["cancelled"] is False
    assert out["reason"] == "folder-picker-failed"
    assert "not bound" in out["detail"]


def test_bridge_without_native_bindings_is_backwards_compatible():
    api = br.JsApi(
        open_window_factory=lambda url: None,
        status_provider=lambda: {"state": "healthy"},
        retry_handler=lambda: None,
        log_opener=lambda: None,
    )

    assert api.server_status() == {"state": "healthy"}
    out = api.choose_research_folder()
    assert out["reason"] == "folder-picker-unavailable"
    assert out["cancelled"] is False
