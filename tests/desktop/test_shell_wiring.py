# -*- coding: utf-8 -*-
"""shell 只是接线,但接线也会错。这里用假的 webview 模块把接线钉住。

真窗口的行为不在这里验(仓库没有窗口测试设施),在 Task 7 人工验收。
"""
from __future__ import annotations

import os
import types
from pathlib import Path

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


def _parse_boot_state(code: str) -> dict:
    """从 `window.glBoot && window.glBoot.setState({...});` 里把 JSON 部分抠出来。"""
    import json

    payload = code.split("setState(", 1)[1].rsplit(");", 1)[0]
    return json.loads(payload)


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
    """安全靠构造,不靠算术:即便这条测试以后被改到跨过遮罩阈值,注入的
    spawner 也保证不会有真的看门狗被拉起 —— 不依赖 iter([...]) 恰好在 3 次
    阈值前一步停下这件"巧合"。"""
    fake = _FakeWebview()
    probes = iter([True, False])
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: next(probes), spawner=lambda: _spawn_result())
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


def test_recovery_hides_the_overlay_exactly_once():
    """Review round 4 —— round 3 relaxed this test's assertion to `>=` to
    accommodate an unconditional per-tick hide, but `_FakeWindow.evaluate_js`
    only ever appends, so a later list is always a superset of an earlier one
    and `len(hides2) >= len(hides)` cannot fail under *any* implementation —
    restoring the guard, and the guard couldn't be pinned. Round 4 replaces
    the unconditional-hide approach with a structural fix (a
    `_connection_generation` counter checked in `_on_loaded`, see
    `test_late_load_show_is_skipped_if_a_recovery_already_bumped_the_generation`
    below) that closes the same race *and* lets this strict assertion come
    back for real: hide only fires on the `hide_overlay` edge, exactly once,
    and never again while merely staying healthy."""
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


def test_loaded_and_closed_handlers_are_zero_argument_closures():
    """真 pywebview 按签名内省调度:如果 handler 声明 1 个形参(哪怕带默认值),
    pywebview 会认为它要 1 个参数、传参进来,那个参数不一定还是我们想要的 win,
    会把默认值捕获的 win 顶掉。handler 必须是不接受任何参数的闭包 —— 天然免疫,
    不依赖调度器到底怎么内省。两条绑定(loaded 和 closed)都要钉住,不能只钉
    其中一条 —— 上一轮内省守护只钉了 loaded,closed 是这一轮才加的绑定。"""
    import inspect

    fake = _FakeWebview()
    sh.create_shell(webview_module=fake)
    win = fake.windows[0]
    for event_name in ("loaded", "closed"):
        handler = getattr(win.events, event_name).handlers[0]
        assert inspect.signature(handler).parameters == {}, (
            f"{event_name} handler 不能声明形参(哪怕带默认值)—— 必须是零参数闭包"
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


def test_start_delegates_to_webview_start_with_the_startup_callback(tmp_path, monkeypatch):
    # start() 现在也会 mkdir _WEBVIEW_STORAGE_PATH(见 Task 7b minor 2)—— 换成
    # tmp_path 下的一个目录,免得这条无关的测试在真机 %LOCALAPPDATA% 下建目录。
    monkeypatch.setattr(sh, "_WEBVIEW_STORAGE_PATH", tmp_path / "webview2-profile")
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    s.start()
    assert fake.started is not None
    func, args, kw = fake.started
    assert func == s._startup
    assert kw.get("private_mode") is False


# ── Task 7b —— storage_path 必须专属,不能落回 WebView2 的跨应用共享默认目录 ──
#
# 没有显式 storage_path 时,pywebview 在 private_mode=False 下会把 profile 放进
# %APPDATA%/pywebview 这个所有没设 storage_path 的 pywebview 应用共享的默认目录。
# Task 7 验收时观测到:这台机器上一个不相关的 pywebview 应用
# (G:\stocks\dist\GuanlanDataManager\GuanlanDataManager.exe)同样没设它,
# 我们的渲染器因此接上了对方已经在跑的浏览器 broker 进程,而不是拿到自己独立的
# 一份 —— WebView2 每个 user-data 目录对应一个浏览器 broker 进程,对方崩溃/
# 强制更新/非正常关闭会连带拖垮我们的渲染器(反之亦然),localhost 的 cookie
# 也不按端口区分(RFC 6265),两个不相关应用之间会串。必须显式传一个 app 专属的
# 目录 —— 删掉这个参数这条测试就会失败。

def test_start_passes_storage_path_matching_the_module_constant(tmp_path, monkeypatch):
    """kwarg 接线测试:用 monkeypatch 把 _WEBVIEW_STORAGE_PATH 换成 tmp_path 下的
    一个目录,这样 start() 里那句 mkdir 不会在真机 %LOCALAPPDATA% 下真的建目录
    —— 真实落点(是不是真的钉在 %LOCALAPPDATA% 而不是随便一个非空字符串)由
    下面 test_webview_storage_path_constant_points_at_local_appdata_or_repo_var_fallback
    单独钉,那条测试从不调用 start(),不会碰真实磁盘。"""
    fake_storage = tmp_path / "webview2-profile"
    monkeypatch.setattr(sh, "_WEBVIEW_STORAGE_PATH", fake_storage)
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake)
    s.start()
    assert fake.started is not None
    _func, _args, kw = fake.started

    storage_path = kw.get("storage_path")
    assert storage_path, (
        "必须显式传 storage_path,否则 WebView2 落回跨应用共享的默认 profile 目录"
        "(%APPDATA%/pywebview),会和这台机器上其它 pywebview 应用共用同一个"
        "浏览器 broker 进程 / 同一份 cookie"
    )
    assert storage_path == str(fake_storage), (
        "storage_path 必须原样传递 shell.py 里那个 app 专属常量的当前值"
    )
    assert kw.get("private_mode") is False, "storage_path 不能悄悄把已经钉住的 private_mode=False 带跑偏"
    assert fake_storage.is_dir(), "start() 必须自己把这个目录建出来(见 minor 2 的说明),不能指望 pywebview 兜底"


def test_webview_storage_path_constant_points_at_local_appdata_or_repo_var_fallback():
    """钉住真实的 _WEBVIEW_STORAGE_PATH 常量本身该落在哪 —— 不调用 start(),
    纯粹的路径断言,不碰真实磁盘。用 parts[-2:] 只钉后两段会被"任意磁盘上某个
    碰巧以这两段结尾的路径"糊弄过去;这里改成钉住完整的、真正打算落到的位置:
    %LOCALAPPDATA% 存在时必须是它,不是仓库 var/(那是 _LOG_PATH/_SHELL_LOG_PATH
    那种一次性诊断产物的地盘,不是可长期保留的浏览器 profile 该待的地方)。"""
    local_appdata = os.environ.get("LOCALAPPDATA")
    expected_root = Path(local_appdata) if local_appdata else (sh._DIR.parents[1] / "var")
    assert sh._WEBVIEW_STORAGE_PATH == expected_root / "Guanlan" / "webview2-profile"


def test_real_webview_start_accepts_a_storage_path_kwarg():
    """`_FakeWebview.start(self, func=None, args=None, **kw)` 吞掉任何 kwarg 的
    名字 —— 如果 storage_path 手滑打成 storage_dir / storagePath,上面两条用假
    webview 的测试照样会绿,只有真机启动时 webview.start() 才会 TypeError。这里
    直接对着真实安装的 pywebview 内省签名,把参数名字钉死。只 import 模块 +
    inspect.signature,不会触发任何 GUI —— guilib.initialize() 只在 webview.start()
    内部被调用,这里从不调用它,在无头测试机上是安全的(已手动确认 headless 通过)。

    两个 kwarg 名字都要钉,不能只钉 storage_path:假 webview 同样吞掉
    private_mode 这个名字,而它是"这份 profile 到底持不持久化"的开关 ——
    一个 private_mode 的笔误只会在真机启动时才 TypeError,和 storage_path
    是同一类缺陷。"""
    import inspect

    import webview

    params = set(inspect.signature(webview.start).parameters)
    assert {"storage_path", "private_mode"} <= params


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
    # 同一进程里如果这条测试跑了不止一次(比如某个 rerun/repeat 插件),
    # "hello world" 这个 key 会永久留在真实的模块级 _last_log_at 里,第二次
    # 就会被限流窗口吞掉、log_path(一个全新的 tmp_path)从未被创建过,
    # read_text 直接炸。必须像另外两条测试一样隔离这张表。
    monkeypatch.setattr(sh, "_last_log_at", {})
    sh._log_shell_event("hello world")
    assert "hello world" in log_path.read_text(encoding="utf-8")


# ── Review round 3 —— IMPORTANT: 引导页可以经第二条路再度变成终态,而且悄无声息 ──
#
# `_startup` 先跑 run_boot_sequence 再起心跳线程;run_boot_sequence 一炸,心跳线程
# 就永远不会被起来 —— 没有监控、没有 status、没有 _advance_boot_window_if_pending,
# 引导页原地卡死,而且这异常死在 pywebview 生的线程上,__main__.py 的
# _run_and_log_crashes 看不到,什么痕迹都不会留下。对称地,_heartbeat_loop 的
# while True 里一次 heartbeat_once 抛异常,就会让往后所有的心跳都消失。

def test_startup_starts_the_heartbeat_thread_even_when_boot_sequence_raises(tmp_path, monkeypatch):
    # 隔离真实的 var/desktop-shell.log —— _startup 的 except 分支会真的调
    # _log_shell_event,不隔离就会把这条测试用的异常消息写进仓库真实的日志文件。
    monkeypatch.setattr(sh, "_SHELL_LOG_PATH", tmp_path / "desktop-shell.log")
    monkeypatch.setattr(sh, "_last_log_at", {})

    fake = _FakeWebview()

    def _boom(**kw):
        raise RuntimeError("boot exploded")

    s = sh.create_shell(webview_module=fake, ensure=_boom)

    started = []

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(sh.threading, "Thread", _FakeThread)

    s._startup()  # 不该向外抛,即便 ensure 炸了
    assert started, "run_boot_sequence 炸了之后,心跳线程还是必须被起起来 —— 否则引导页原地卡死"
    assert started[0] == s._heartbeat_loop


def test_a_failing_heartbeat_tick_does_not_kill_the_loop(tmp_path, monkeypatch):
    # 同上:隔离真实日志文件,不让测试异常消息污染仓库里的 var/desktop-shell.log。
    monkeypatch.setattr(sh, "_SHELL_LOG_PATH", tmp_path / "desktop-shell.log")
    monkeypatch.setattr(sh, "_last_log_at", {})

    fake = _FakeWebview()
    calls = {"n": 0}

    def _flaky_prober():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network stack exploded")
        return True

    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=_flaky_prober)
    s.run_boot_sequence(fake.windows[0])
    s._protected_heartbeat_tick()  # 第一次探测直接炸 —— 不该向外抛
    s._protected_heartbeat_tick()  # 第二次证明循环没被那次异常打死:探测恢复,状态照常刷新
    assert s.api.server_status()["state"] == "healthy"
    assert calls["n"] == 2


def test_navigate_failure_is_logged_instead_of_crashing_the_boot_sequence(monkeypatch):
    """load_url 是这一轮唯一没走 _eval 式留痕的 pywebview 调用 —— 一扇从没
    show 过的窗口(WebView2 运行时坏掉/GPU 挂起)会让它等 20s 后抛
    WebViewException。壳绝不能因为一次导航失败就把 run_boot_sequence /
    心跳线程带崩,但要留痕。"""
    logged = []
    monkeypatch.setattr(sh, "_log_shell_event", lambda msg: logged.append(msg))

    class _BoomWindow:
        def load_url(self, url):
            raise RuntimeError("WebView2 runtime is gone")

    sh._navigate(_BoomWindow(), sh.APP_URL)
    assert logged and "WebView2 runtime is gone" in logged[0]


# ── Review round 3 —— Minor 4: shell log 要限流/去重,不能无界增长 ──────────

def test_log_shell_event_dedupes_identical_messages_within_the_window(tmp_path, monkeypatch):
    log_path = tmp_path / "desktop-shell.log"
    monkeypatch.setattr(sh, "_SHELL_LOG_PATH", log_path)
    monkeypatch.setattr(sh, "_last_log_at", {})
    fake_now = [1000.0]
    monkeypatch.setattr(sh.time, "monotonic", lambda: fake_now[0])

    sh._log_shell_event("boom")
    sh._log_shell_event("boom")  # 限流窗口内的重复 —— 不该再写一行
    content = log_path.read_text(encoding="utf-8")
    assert content.count("boom") == 1, "同一条消息在限流窗口内不该被重复写盘"

    fake_now[0] += sh._LOG_DEDUPE_SECONDS + 1
    sh._log_shell_event("boom")  # 窗口过了:同样的消息应该能再被记一次
    content2 = log_path.read_text(encoding="utf-8")
    assert content2.count("boom") == 2, "限流窗口一过,同样的消息应该还能被记录"


# ── Review round 3 —— Minor 5: 重试要有即时反馈,不能第一次点和以前一样死气沉沉 ──

def test_retry_pushes_immediate_feedback_to_the_boot_page():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _timeout_outcome())
    s.run_boot_sequence(fake.windows[0])
    before = len(fake.windows[0].evaluated)
    s.api.retry()
    assert len(fake.windows[0].evaluated) > before, "点了重试应该立刻看到点反应,不能像以前一样死气沉沉"
    assert any("正在重试" in c for c in fake.windows[0].evaluated)


def test_retry_gives_no_feedback_once_already_past_the_boot_page():
    """已经翻过引导页之后,重试不该再往 main_window 里塞 glBoot 状态(那扇窗此刻
    多半已经不是 boot.html 了,glBoot 也早不存在,推不推都无所谓,但没必要推)。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome())
    s.run_boot_sequence(fake.windows[0])
    before = len(fake.windows[0].evaluated)
    s.api.retry()
    assert len(fake.windows[0].evaluated) == before


# ── Review round 4 —— IMPORTANT 1: 重试反馈把控件和污染警告一起抹掉了 ─────────
#
# boot.html 的 setState 是无条件覆盖式的:detail 被清空、warn 被清空并隐藏,
# stuck 因此算出 false → 按钮 (`#acts`) 被隐藏。round 3 那个只塞
# {"phase": "starting", "message": "正在重试…"} 的推送会让 重试/看日志 两个按钮
# 在第一次点击后就消失,GUANLAN_PORT 污染警告也永久消失且没有任何东西会再推一次
# 完整状态回去 —— 用户只能等第三次探测失败画上 overlay.js 才能重新拿到控件。

def test_retry_feedback_preserves_phase_detail_and_warning():
    """round 3 那条覆盖测试只断言"评估过的代码里出现了'正在重试'这几个字",
    在这个回归存在的情况下照样能过 —— 这里改成解析真正推送的 JSON state,
    钉住 phase/detail/warning 必须原样保留,只有 message 被换成"正在重试…"。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake,
                        ensure=lambda **kw: _timeout_outcome(),
                        contamination=lambda: "检测到 GUANLAN_PORT=9998")
    s.run_boot_sequence(fake.windows[0])
    s.api.retry()

    boot_calls = [c for c in fake.windows[0].evaluated if "glBoot.setState" in c]
    state = _parse_boot_state(boot_calls[-1])

    assert state["message"] == "正在重试…"
    assert state["phase"] == "timeout", "phase 必须保留 —— 否则 boot.html 算出 stuck=false,按钮消失"
    assert state["detail"], "detail 必须保留,不能被清空成空字符串/None"
    assert "9998" in (state.get("warning") or ""), "GUANLAN_PORT 污染警告不能被这次推送顺手抹掉"


def test_retry_feedback_reflects_whatever_the_latest_pushed_state_was():
    """last_boot_state 必须跟着 run_boot_sequence 期间真正推送过的最后一条状态走,
    不是钉死某个常量 —— 这里用 spawn_failed 的结局验证 phase 也能正确带过去。"""
    fake = _FakeWebview()

    def _spawn_failed_outcome(**kw):
        from guanlan_v2.desktop.supervisor import EnsureOutcome
        return EnsureOutcome("spawn_failed", False, 0.4, "看门狗脚本缺失")

    s = sh.create_shell(webview_module=fake, ensure=_spawn_failed_outcome)
    s.run_boot_sequence(fake.windows[0])
    s.api.retry()

    boot_calls = [c for c in fake.windows[0].evaluated if "glBoot.setState" in c]
    state = _parse_boot_state(boot_calls[-1])
    assert state["phase"] == "spawn_failed"
    assert "看门狗脚本缺失" in state["detail"]


# ── Review round 4 —— IMPORTANT 2: 结构性修法(连接代数)取代无条件 hide ────────
#
# _on_loaded 在决定要不要补一次 show 之前,先记下当时的 _connection_generation;
# 心跳每完成一次 degraded→healthy 的恢复就把这个代数加一。如果 _on_loaded 捕获
# 代数之后、真正调用 _eval(show) 之前,heartbeat 已经抢先完成了一次恢复(代数
# 变了),这次 show 就该被跳过 —— 用一个整数比较把竞态窗口从"整个 _eval 调用的
# IPC 往返耗时"收窄到"捕获代数和紧跟着那一次比较之间"这一条 Python 语句的距离。

def test_late_load_show_is_skipped_if_a_recovery_already_bumped_the_generation():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()  # 进入 degraded
    win = fake.windows[0]

    # 模拟竞态本身:_on_loaded 会在这一刻捕获到的代数……
    stale_generation = s._connection_generation
    # ……但在它真正判断/发 eval 之前,heartbeat 抢先完成了一次恢复
    # (heartbeat_once 的顺序是先置 status 再加代数,这里照抄同一顺序)。
    s._status = {"state": "healthy", "detail": ""}
    s._connection_generation += 1

    before = len(win.evaluated)
    s._maybe_show_overlay_for_late_load(win, stale_generation)
    assert len(win.evaluated) == before, "捕获时的代数已经过期,这次 show 必须被跳过"


def test_late_load_show_fires_when_still_genuinely_degraded():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()  # 进入 degraded
    win = fake.windows[0]
    s._maybe_show_overlay_for_late_load(win, s._connection_generation)
    assert any("glShellOverlay.show" in c for c in win.evaluated)


# ── Review round 5 —— IMPORTANT 1: show 和 hide 曾经各自钉在两套不同的状态机上 ──
#
# _maybe_show_overlay_for_late_load(round 4 版本)看的是 self._status ——
# 一个"探测级"信号,heartbeat_once 在第 1 次探测失败就已经把它标记 degraded
# (这是 round 2 Important 3 的有意设计,server_status() 要新鲜)。而 hide 广播
# 和代数递增看的是 decision.hide_overlay —— 一个"闩级"信号,ConnectionMonitor
# 只在连续第 3 次失败时才会锁上那把闩。第 1、2 次失败时 _status 已经是
# degraded,但闩没锁、没有 show 广播、也就没有"将来一定有一次 hide 广播"这个
# 保证 —— 如果这时候恰好有一扇窗口加载完(这个 UI 里"加载完"并不罕见:顶栏在
# 不同 .html 文档之间跳转本来就是整页导航),late-load 就会画上一层帷幄,
# 而将来连接恢复时 hide_overlay 恒为 False(闩从没锁过),没有任何东西会去揭它。

def test_late_load_show_is_skipped_below_the_broadcast_threshold_at_one_failure():
    """round 4 修复前的样子会在这里显示一层永远没人揭的幕布 —— 这是要求里
    "sub-threshold RED"那条:第 1 次失败 + 一次加载 → 永不消失的遮罩。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    s.heartbeat_once()  # 第 1 次失败:_status 已经是 degraded,但闩没锁,没有广播
    assert s.api.server_status()["state"] == "degraded"

    win = fake.windows[0]
    before = len(win.evaluated)
    s._maybe_show_overlay_for_late_load(win, s._connection_generation)
    assert len(win.evaluated) == before, (
        "第 1 次失败还没到闩的阈值,late-load 不该显示一层将来没有任何 hide 会去揭的幕布"
    )


def test_late_load_show_is_skipped_below_the_broadcast_threshold_at_two_failures():
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    s.heartbeat_once()
    s.heartbeat_once()  # 第 2 次失败,依然没到闩的阈值(3)
    win = fake.windows[0]
    before = len(win.evaluated)
    s._maybe_show_overlay_for_late_load(win, s._connection_generation)
    assert len(win.evaluated) == before


def test_late_load_show_still_fires_once_the_broadcast_latch_is_set():
    """确认修复没有矫枉过正:第 3 次失败一旦真的锁上闩、广播过 show,
    late-load 该显示的还是要显示(否则和 test_late_load_show_fires_when_
    still_genuinely_degraded 重复,这条额外确认"闩"本身是有效信号,不是
    "永远不显示"式的过度收紧)。"""
    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: False, spawner=lambda: _spawn_result())
    s.run_boot_sequence(fake.windows[0])
    for _ in range(3):
        s.heartbeat_once()
    assert s._overlay_broadcast is True
    win = fake.windows[0]
    s._maybe_show_overlay_for_late_load(win, s._connection_generation)
    assert any("glShellOverlay.show" in c for c in win.evaluated)


# ── Review round 5 —— IMPORTANT 2: 清扫循环不能在唯一"绝不能抛"的函数里抛 ─────
#
# _last_log_at 被好几个线程无锁地读写(心跳线程、每扇窗口各自的 loaded 事件
# 线程、js-bridge 线程)。round 4 加的清扫循环在 _log_shell_event 自己的
# try/except 之外遍历 .items() —— 如果遍历期间另一个线程插入了新键,CPython
# 会抛 RuntimeError: dictionary changed size during iteration,这条异常会
# 从 _log_shell_event 里逃出去,而它的唯一调用方是 except 块;同一个 try
# 接不住自己 except 块里再抛出来的异常,于是这条异常会直接穿过
# _protected_heartbeat_tick 的 try、经 _heartbeat_loop 的 while True 把
# 心跳线程带走 —— 正是 round 3 才堵上的洞,这次是被"堵洞的工具自己"重新捅开。

class _ExplodingItemsDict(dict):
    """模拟"清扫循环恰好撞上另一个线程并发插入新键"的效果:不用真的起线程,
    直接让 .items() 在被调用的那一刻抛出 CPython 真实会抛的那个 RuntimeError。"""
    def items(self):
        raise RuntimeError("dictionary changed size during iteration")


def test_log_shell_event_never_lets_a_sweep_failure_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_SHELL_LOG_PATH", tmp_path / "desktop-shell.log")
    monkeypatch.setattr(sh, "_last_log_at", _ExplodingItemsDict())
    sh._log_shell_event("boom")  # 不该向外抛 —— 这是它唯一的契约


# ── Review round 4 —— Minor: _last_log_at 本身不能无界增长 ───────────────────

def test_last_log_at_does_not_grow_unbounded_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(sh, "_SHELL_LOG_PATH", tmp_path / "desktop-shell.log")
    monkeypatch.setattr(sh, "_last_log_at", {})
    fake_now = [0.0]
    monkeypatch.setattr(sh.time, "monotonic", lambda: fake_now[0])

    for i in range(50):
        fake_now[0] += sh._LOG_DEDUPE_SECONDS + 1  # 每次都让上一条彻底过期
        sh._log_shell_event(f"message-{i}")

    assert len(sh._last_log_at) == 1, "每条消息的去重窗口都已经过期,去重表不该无限堆积旧键"


# ── Final review A — Minor 1: 两处事件绑定的吞异常此前完全不留痕 ─────────────
#
# win.events.loaded += ... / win.events.closed += ... 各自包着一个裸
# `except Exception: pass`,是 shell.py 里唯一一处没有 _log_shell_event 的吞
# 异常。真出问题时后果很大且完全无声:GL_DESKTOP/overlay.js 再也不会被注入到
# 这扇窗口,掉线遮罩再也不会在它身上出现,而 var/desktop-shell.log 里不会有
# 任何一行字。

class _EventBindingBoomEvent:
    """`+=` 本身抛异常 —— 模拟 pywebview 的 Event.__iadd__ 出问题的情形。"""

    def __iadd__(self, fn):
        raise RuntimeError("event binding is gone")


class _EventBindingBoomWebview(_FakeWebview):
    def create_window(self, title, url=None, **kw):
        w = _FakeWindow(title, url, **kw)
        w.events = types.SimpleNamespace(
            loaded=_EventBindingBoomEvent(), closed=_EventBindingBoomEvent()
        )
        self.windows.append(w)
        return w


def test_event_binding_failures_are_logged_instead_of_vanishing(monkeypatch):
    logged = []
    monkeypatch.setattr(sh, "_log_shell_event", lambda msg: logged.append(msg))

    fake = _EventBindingBoomWebview()
    sh.create_shell(webview_module=fake)

    assert len(logged) == 2, "loaded 和 closed 两次绑定失败都要各留一笔,不能只记一次"
    assert any("loaded" in m and "event binding is gone" in m for m in logged)
    assert any("closed" in m and "event binding is gone" in m for m in logged)


# ── Final review A — Minor 2 (C6): 失败的导航不该把 _boot_pending 清掉 ────────
#
# _navigate 吞异常并留痕,但此前调用方无条件把 _boot_pending 设 False —— 于是
# 一次没有真正发生的导航被当成"已经翻页",引导页从此再没有任何代码路径会去
# 调 load_url。两处调用点(run_boot_sequence 的健康分支、
# _advance_boot_window_if_pending)都要验证。

class _FlakyLoadWindow(_FakeWindow):
    """第一次 load_url 抛,第二次成功 —— 模拟"这一次导航失败,下一拍心跳的
    重试成功"。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._load_attempts = 0

    def load_url(self, url):
        self._load_attempts += 1
        if self._load_attempts == 1:
            raise RuntimeError("WebView2 runtime is gone")
        self.loaded_urls.append(url)


class _FlakyLoadWebview(_FakeWebview):
    def create_window(self, title, url=None, **kw):
        w = _FlakyLoadWindow(title, url, **kw)
        self.windows.append(w)
        return w


def test_boot_pending_survives_a_failed_navigate_and_retries_on_next_heartbeat(monkeypatch):
    monkeypatch.setattr(sh, "_log_shell_event", lambda msg: None)
    fake = _FlakyLoadWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _healthy_outcome(),
                        prober=lambda: True)

    s.run_boot_sequence(fake.windows[0])
    assert fake.windows[0].loaded_urls == [], "第一次导航失败,不该记为已经跳转"
    assert s._boot_pending is True, (
        "导航失败后 _boot_pending 必须保持 True —— 清掉它就再没有任何代码路径"
        "会重新尝试导航,引导页原地卡死"
    )

    s.heartbeat_once()  # 心跳重试导航,这次成功
    assert fake.windows[0].loaded_urls == [sh.APP_URL], "心跳应该重试导航并这次成功"
    assert s._boot_pending is False


def test_advance_boot_window_retries_when_navigate_keeps_failing(monkeypatch):
    """_advance_boot_window_if_pending 这条调用点也要单独验证 —— 不能只验
    run_boot_sequence 那一条,两处是各自独立的调用点。"""
    monkeypatch.setattr(sh, "_log_shell_event", lambda msg: None)
    fake = _FlakyLoadWebview()
    s = sh.create_shell(webview_module=fake, ensure=lambda **kw: _timeout_outcome(),
                        prober=lambda: True)
    s.run_boot_sequence(fake.windows[0])
    assert s._boot_pending is True, "超时结局本来就不清 _boot_pending"

    s._advance_boot_window_if_pending()  # 第一次尝试:load_url 抛
    assert fake.windows[0].loaded_urls == []
    assert s._boot_pending is True, "失败的导航不该清掉 _boot_pending"

    s._advance_boot_window_if_pending()  # 第二次尝试:成功
    assert fake.windows[0].loaded_urls == [sh.APP_URL]
    assert s._boot_pending is False


# ── Final review A — Minor 5 (S2): 看日志失败此前彻底无声 ────────────────────
#
# JsApi.open_log 把这里抛出的异常包成 {ok: False, ...} 还给网页,但 JsApi 故意
# 不 import shell、没有留痕能力 —— 如果 _on_open_log 自己不记一笔,os.startfile
# 失败时 Python 侧和 UI 侧会同时哑掉,一丝痕迹都留不下。

def test_open_log_failure_is_logged_instead_of_vanishing(monkeypatch):
    logged = []
    monkeypatch.setattr(sh, "_log_shell_event", lambda msg: logged.append(msg))

    def _boom(path):
        raise OSError("no handler registered for .log")

    fake = _FakeWebview()
    s = sh.create_shell(webview_module=fake, file_opener=_boom)
    out = s.api.open_log()

    assert out["ok"] is False, "JsApi 的失败语义不能被这处新加的留痕改变"
    assert logged and "no handler registered for .log" in logged[0]
