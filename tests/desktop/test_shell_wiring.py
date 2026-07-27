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
        self.events = types.SimpleNamespace(loaded=_FakeEvent(), closed=_FakeEvent())
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


def _spawn_result(ok: bool = True, detail: str = "spawned"):
    from guanlan_v2.desktop.supervisor import SpawnResult
    return SpawnResult(ok, 4321 if ok else None, detail)


# ── Review round 2 —— CRITICAL 1: 引导页不该是终态 ──────────────────────────

def test_heartbeat_takes_the_window_off_the_boot_page_once_the_server_recovers():
    """ensure 超时后,`重试`/看日志只显示在这一状态下 —— 但点了以前什么都不会发生,
    因为除了 run_boot_sequence(只跑一次)没有别的代码路径会 load_url(APP_URL)。
    修法(b):心跳一旦探到已连通,就把还停在引导页的窗口放行 —— 不需要重新跑一遍
    run_boot_sequence(不会重复拉看门狗),也不需要给 retry 加任何新状态。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake,
                        ensure=lambda **kw: _timeout_outcome(),
                        prober=lambda: True)
    s.run_boot_sequence(fake.windows[0])
    assert fake.windows[0].loaded_urls == [], "超时之后不该自己跳走"
    s.heartbeat_once()
    assert fake.windows[0].loaded_urls == [sh.APP_URL], (
        "心跳发现已连通后,引导页必须放行到帷幄页 —— 否则只能关窗重开才能恢复"
    )


def test_boot_page_is_not_reopened_once_it_has_already_advanced():
    """放行只发生一次:健康启动已经把主窗口带去 APP_URL 之后,后续心跳不该再调 load_url。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: True)
    s.run_boot_sequence(fake.windows[0])
    assert fake.windows[0].loaded_urls == [sh.APP_URL]
    s.heartbeat_once()
    s.heartbeat_once()
    assert fake.windows[0].loaded_urls == [sh.APP_URL], "已经放行过的窗口不该被反复 load_url"


# ── IMPORTANT 2: 关闭的窗口必须从镜像列表里剔除 ──────────────────────────────

def test_closed_windows_stop_receiving_evaluate_js_calls():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    s.open_ui_window("http://127.0.0.1:9999/ui/screen/x.html")
    second = fake.windows[1]

    for handler in second.events.closed.handlers:
        handler()
    before = len(second.evaluated)

    for _ in range(3):  # 触发一次掉线遮罩
        s.heartbeat_once()

    assert len(second.evaluated) == before, "已关闭的窗口不该再收到任何 evaluate_js 调用"
    assert any("glShellOverlay.show" in c for c in fake.windows[0].evaluated), "存活窗口应该收到遮罩"


def test_double_close_does_not_raise():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    win = fake.windows[0]
    for handler in win.events.closed.handlers:
        handler()
    for handler in win.events.closed.handlers:
        handler()  # 重复触发 —— 不该抛 ValueError


# ── IMPORTANT 3: server_status 必须每次心跳都刷新,不能只在状态跳变时刷新 ─────

def test_status_reflects_the_latest_probe_even_below_the_overlay_threshold():
    fake = _FakeWebview()
    probes = iter([True, False])
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: next(probes))
    s.run_boot_sequence(fake.windows[0])
    s.heartbeat_once()
    assert s.api.server_status()["state"] == "healthy"
    s.heartbeat_once()  # 第一次探测失败,还没到 3 次的遮罩阈值
    assert s.api.server_status()["state"] != "healthy", (
        "第一次探测失败就该反映在 status 上,不必等到第三次触发遮罩"
    )


def test_status_flips_back_to_healthy_immediately_on_recovery():
    fake = _FakeWebview()
    probes = iter([False, False, False, True])
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: next(probes), spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()
    assert s.api.server_status()["state"] == "degraded"
    s.heartbeat_once()
    assert s.api.server_status()["state"] == "healthy"


# ── IMPORTANT 4: 心跳/加载/日志机器本身的测试 ────────────────────────────────

def test_three_failures_show_overlay_and_spawn_exactly_once():
    fake = _FakeWebview()
    spawn_calls = []
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False,
                        spawner=lambda: (spawn_calls.append(1), _spawn_result())[1])
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()
    assert len(spawn_calls) == 1
    shows = [c for c in fake.windows[0].evaluated if "glShellOverlay.show" in c]
    assert len(shows) == 1

    # 再失败几次:不该再多一次 show 或 spawn —— 一次掉线只拉一次看门狗。
    s.heartbeat_once()
    s.heartbeat_once()
    assert len(spawn_calls) == 1
    shows2 = [c for c in fake.windows[0].evaluated if "glShellOverlay.show" in c]
    assert len(shows2) == 1


def test_recovery_hides_overlay_exactly_once():
    fake = _FakeWebview()
    probes = iter([False, False, False, True, True])
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: next(probes), spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()
    s.heartbeat_once()  # 恢复
    hides = [c for c in fake.windows[0].evaluated if "glShellOverlay.hide" in c]
    assert len(hides) == 1
    s.heartbeat_once()  # 继续健康:不该再多一次 hide
    hides2 = [c for c in fake.windows[0].evaluated if "glShellOverlay.hide" in c]
    assert len(hides2) == 1


def test_loaded_handler_is_a_zero_argument_closure():
    """真 pywebview 按签名内省调度:如果 handler 声明 1 个形参(哪怕带默认值),
    pywebview 会认为它要 1 个参数、传参进来,那个参数不一定还是我们想要的 win,
    会把默认值捕获的 win 顶掉。handler 必须是不接受任何参数的闭包 —— 天然免疫,
    不依赖调度器到底怎么内省。"""
    import inspect

    fake = _FakeWebview()
    sh.create_shell(webview_module=fake)
    win = fake.windows[0]
    handler = win.events.loaded.handlers[0]
    assert inspect.signature(handler).parameters == {}, (
        "loaded handler 不能声明形参(哪怕带默认值)—— 必须是零参数闭包"
    )


def test_on_loaded_injects_gl_desktop_flag_and_overlay_script():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    win = fake.windows[0]
    s._on_loaded(win)
    assert any("GL_DESKTOP = true" in c for c in win.evaluated)
    assert any("glShellOverlay" in c for c in win.evaluated)  # overlay.js 本体注入


def test_window_opened_while_degraded_gets_the_overlay_immediately():
    """"Also fix": 掉线期间打开的新窗口不该是一扇看起来正常的假窗口 —— 上一次
    show 只广播给了当时已存在的窗口,_on_loaded 必须在加载时补一次。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()  # 进入 degraded

    s.open_ui_window("http://127.0.0.1:9999/ui/screen/x.html")
    new_win = fake.windows[-1]
    s._on_loaded(new_win)
    assert any("glShellOverlay.show" in c for c in new_win.evaluated), (
        "掉线期间新开的窗口也该立刻看见遮罩"
    )


def test_on_open_log_calls_the_injected_file_opener_not_the_real_os():
    fake = _FakeWebview()
    opened = []
    s = sh.create_shell(webview_module=fake, file_opener=lambda path: opened.append(path))
    out = s.api.open_log()
    assert out["ok"] is True
    assert opened and opened[0].endswith("server-9999.log")


def test_on_retry_wakes_the_heartbeat():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    assert not s._retry_requested.is_set()
    s.api.retry()
    assert s._retry_requested.is_set()


def test_start_delegates_to_webview_start_with_the_startup_callback():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    s.start()
    assert fake.started is not None
    func, args, kw = fake.started
    assert func == s._startup
    assert kw.get("private_mode") is False


# ── Also fix: 看门狗拉起失败也要在 status 上留痕 ─────────────────────────────

def test_spawn_failure_is_folded_into_status():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False,
                        spawner=lambda: _spawn_result(False, "watchdog script missing"))
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()
    status = s.api.server_status()
    assert status["state"] == "degraded"
    assert "watchdog script missing" in status["detail"]


# ── Also fix: create_shell(prober=...) 必须真的接到 boot 路径,不能悄悄回落 ──

def test_create_shell_prober_reaches_the_boot_path_without_explicit_ensure():
    fake = _FakeWebview()
    calls = []

    def fake_prober():
        calls.append(1)
        return True

    s = sh.create_shell(webview_module=fake, prober=fake_prober)
    s.run_boot_sequence(fake.windows[0])
    assert calls, "默认 ensure 必须真的用上注入的 prober,不能悄悄命中真实网络"
    assert fake.windows[0].loaded_urls == [sh.APP_URL]


# ── Also fix: _eval 的静默 except 必须留痕 ──────────────────────────────────

def test_eval_failures_are_logged_instead_of_vanishing(monkeypatch):
    logged = []
    monkeypatch.setattr(sh, "_log_shell_event", lambda msg: logged.append(msg))

    class _BoomWindow:
        def evaluate_js(self, code):
            raise RuntimeError("window is gone")

    sh._eval(_BoomWindow(), "window.x = 1;")
    assert logged and "window is gone" in logged[0]


def test_log_shell_event_appends_to_the_shell_log_file(tmp_path, monkeypatch):
    log_path = tmp_path / "desktop-shell.log"
    monkeypatch.setattr(sh, "_SHELL_LOG_PATH", log_path)
    sh._log_shell_event("hello world")
    assert "hello world" in log_path.read_text(encoding="utf-8")
