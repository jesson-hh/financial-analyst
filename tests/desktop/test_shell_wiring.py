# -*- coding: utf-8 -*-
"""shell 只是接线,但接线也会错。这里用假的 webview 模块把接线钉住。

真窗口的行为不在这里验(仓库没有窗口测试设施),在 Task 7 人工验收。
"""
from __future__ import annotations

import types

from guanlan_v2.desktop import shell as sh


class _FakeWindow:
    def __init__(self, title, url, **kw):
        self.title, self.url, self.kw = title, url, kw
        self.loaded_urls = []
        self.evaluated = []
        self.events = types.SimpleNamespace(loaded=_FakeEvent())
    def load_url(self, url): self.loaded_urls.append(url)
    def evaluate_js(self, code): self.evaluated.append(code); return None


class _FakeEvent:
    def __init__(self): self.handlers = []
    def __iadd__(self, fn): self.handlers.append(fn); return self


class _FakeWebview:
    def __init__(self):
        self.windows = []
        self.started = None
    def create_window(self, title, url=None, **kw):
        w = _FakeWindow(title, url, **kw)
        self.windows.append(w)
        return w
    def start(self, func=None, args=None, **kw):
        self.started = (func, args, kw)


def test_first_window_opens_on_the_boot_page_not_the_app():
    fake = _FakeWebview()
    sh.create_shell(webview_module=fake)
    assert len(fake.windows) == 1
    assert fake.windows[0].url == sh.BOOT_URI, "窗口必须立刻出现,不能等服务器"


def test_first_window_carries_the_js_api():
    fake = _FakeWebview()
    sh.create_shell(webview_module=fake)
    assert "js_api" in fake.windows[0].kw


def test_open_ui_window_creates_another_window_with_the_same_api():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    s.open_ui_window("http://127.0.0.1:9999/ui/screen/x.html")
    assert len(fake.windows) == 2
    assert fake.windows[1].url == "http://127.0.0.1:9999/ui/screen/x.html"
    assert "js_api" in fake.windows[1].kw, "新窗口也要能继续开窗"


def test_bridge_refusal_never_reaches_the_window_factory():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    out = s.api.open_window("file:///C:/Windows/win.ini")
    assert out["ok"] is False and len(fake.windows) == 1


def test_boot_navigates_to_the_app_when_healthy():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake,
                        ensure=lambda **kw: _healthy_outcome())
    s.run_boot_sequence(fake.windows[0])
    assert sh.APP_URL in fake.windows[0].loaded_urls


def test_boot_stays_on_the_boot_page_when_it_times_out():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake,
                        ensure=lambda **kw: _timeout_outcome())
    s.run_boot_sequence(fake.windows[0])
    assert fake.windows[0].loaded_urls == [], "超时不该跳进一个连不上的页面"
    assert any("glBoot.setState" in c for c in fake.windows[0].evaluated)


def test_port_contamination_is_surfaced_on_the_boot_page():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake,
                        ensure=lambda **kw: _timeout_outcome(),
                        contamination=lambda: "检测到 GUANLAN_PORT=9998")
    s.run_boot_sequence(fake.windows[0])
    assert any("9998" in c for c in fake.windows[0].evaluated)


def _healthy_outcome():
    from guanlan_v2.desktop.supervisor import EnsureOutcome
    return EnsureOutcome("healthy", False, 0.0, "服务器已在运行")


def _timeout_outcome():
    from guanlan_v2.desktop.supervisor import EnsureOutcome
    return EnsureOutcome("timeout", True, 90.0, "看门狗已拉起,但 90s 内 9999 仍未监听")
