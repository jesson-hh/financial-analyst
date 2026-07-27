# -*- coding: utf-8 -*-
"""pywebview 接线。真实逻辑在 supervisor 与 bridge,这里只负责把它们连到窗口上。

webview 模块以参数注入(而不是模块层 import),所以接线本身可以用假模块测试,
测试机也不需要 GUI。
"""
from __future__ import annotations

import datetime
import json
import os
import threading
from pathlib import Path
from typing import Callable

from guanlan_v2.desktop import supervisor as sv
from guanlan_v2.desktop.bridge import JsApi

_DIR = Path(__file__).resolve().parent
BOOT_URI = (_DIR / "boot.html").as_uri()
_OVERLAY_JS = (_DIR / "overlay.js").read_text(encoding="utf-8")

WINDOW_TITLE = "观澜"
APP_URL = sv.APP_URL
_WIDTH, _HEIGHT = 1400, 900
_HEARTBEAT_SECONDS = 10.0
_LOG_PATH = _DIR.parents[1] / "var" / "server-9999.log"
_SHELL_LOG_PATH = _DIR.parents[1] / "var" / "desktop-shell.log"


class Shell:
    def __init__(self, *, webview_module, ensure: Callable, prober: Callable,
                 spawner: Callable, contamination: Callable,
                 file_opener: Callable[[str], None]) -> None:
        self._wv = webview_module
        self._ensure = ensure
        self._probe = prober
        self._spawn = spawner
        self._contamination = contamination
        self._file_opener = file_opener
        self._monitor = sv.ConnectionMonitor()
        self._windows: list = []
        self._status: dict = {"state": "checking", "detail": ""}
        # 引导页只有 main_window 一扇;健康后放行一次就翻页,不会再被重复导航。
        self._boot_pending = True
        self.api = JsApi(open_window_factory=self.open_ui_window,
                         status_provider=lambda: dict(self._status),
                         retry_handler=self._on_retry,
                         log_opener=self._on_open_log)
        self._retry_requested = threading.Event()
        self.main_window = self._new_window(BOOT_URI, WINDOW_TITLE)

    # ── 窗口 ────────────────────────────────────────────────────────
    def _new_window(self, url: str, title: str):
        win = self._wv.create_window(title, url=url, js_api=self.api,
                                     width=_WIDTH, height=_HEIGHT)
        self._windows.append(win)
        try:
            # 零参数闭包 —— 真 pywebview 按签名内省调度,声明形参(哪怕带默认值)
            # 会被当成"要 1 个参数",传进来的东西不一定还是这扇 win,会把默认值
            # 捕获的 win 顶掉。零形参对内省天然免疫,不用去猜调度器怎么内省。
            win.events.loaded += lambda: self._on_loaded(win)
        except Exception:  # noqa: BLE001 —— 事件挂不上不该拖垮建窗
            pass
        try:
            win.events.closed += lambda: self._on_closed(win)
        except Exception:  # noqa: BLE001 —— 同上
            pass
        return win

    def open_ui_window(self, url: str) -> None:
        """给 bridge 用的窗口工厂。URL 已被 bridge 的安全闸放行过。"""
        self._new_window(url, WINDOW_TITLE)

    def _on_closed(self, win) -> None:
        """真 pywebview 关窗会剔除它自己的窗口注册表,我们的镜像列表也必须剔除 ——
        否则以后每次心跳都会在这具窗口"尸体"上等 evaluate_js 的 20s 超时
        (_pywebview_ready_call 的 event.wait(20)),一具尸体就能拖垮整条心跳线程。"""
        try:
            self._windows.remove(win)
        except ValueError:
            pass  # 已经被移除过(比如 closed 被重复触发)—— 不是错误

    def _on_loaded(self, win) -> None:
        # GL_DESKTOP 是给顶栏的可移植信号;顶栏同时也认 window.pywebview,故无竞态。
        _eval(win, "window.GL_DESKTOP = true;")
        _eval(win, _OVERLAY_JS)
        if self._status.get("state") == "degraded":
            # 这扇窗是在服务器已经掉线期间才打开的 —— 上一次 show 只广播给了当时
            # 已经存在的窗口,不补这一手,用户会看到一扇"看起来正常"的假窗口。
            _eval(win, "window.glShellOverlay && window.glShellOverlay.show('正在等待服务器恢复…');")

    # ── 启动序列 ────────────────────────────────────────────────────
    def run_boot_sequence(self, win) -> None:
        warning = self._contamination()

        def _progress(msg: str) -> None:
            self._push_boot(win, {"phase": "starting", "message": msg, "warning": warning})

        self._push_boot(win, {"phase": "checking", "message": "正在检查 9999", "warning": warning})
        outcome = self._ensure(on_progress=_progress)
        self._status = {"state": outcome.state, "detail": outcome.detail}

        if outcome.state == "healthy":
            win.load_url(APP_URL)
            self._boot_pending = False
            return
        self._push_boot(win, {"phase": outcome.state, "message": "启动失败",
                              "detail": outcome.detail, "warning": warning})

    def _push_boot(self, win, state: dict) -> None:
        _eval(win, f"window.glBoot && window.glBoot.setState({json.dumps(state, ensure_ascii=False)});")

    def _on_retry(self) -> None:
        self._retry_requested.set()

    def _on_open_log(self) -> None:
        """用系统默认程序打开 var/server-9999.log。只读,不动服务器。"""
        self._file_opener(str(_LOG_PATH))

    # ── 心跳 ────────────────────────────────────────────────────────
    def heartbeat_once(self) -> None:
        decision = self._monitor.observe(self._probe())
        if decision.connected:
            # status 必须每次探测都刷新,不能只在"进入/离开遮罩"的跳变沿刷新 ——
            # 否则超时之后的引导页会让 server_status() 永远读到 "timeout"
            # (监测器压根没见过那次超时,recover 时 hide_overlay 也不会是 True)。
            self._status = {"state": "healthy", "detail": ""}
            if decision.hide_overlay:
                for win in list(self._windows):
                    _eval(win, "window.glShellOverlay && window.glShellOverlay.hide();")
            # 引导页不是终态:心跳一旦探到已连通,就把还停在引导页的主窗口放行到
            # 帷幄页 —— 这也是 重试/看日志 按钮(只会在 ensure 超时后显形)唯一
            # 会真正生效的地方:run_boot_sequence 只在启动那一刻跑一次,之后只有
            # 心跳还在持续探测,离开引导页这件事必须由心跳来做。
            self._advance_boot_window_if_pending()
            return

        self._status = {"state": "degraded",
                        "detail": f"连接中断(连续 {decision.consecutive_failures} 次探测失败)"}
        if decision.show_overlay:
            for win in list(self._windows):
                _eval(win, "window.glShellOverlay && window.glShellOverlay.show('正在等待服务器恢复…');")
            result = self._spawn()      # 一次掉线只拉一次 —— 由 ConnectionMonitor 保证
            if not result.ok:
                self._status = {"state": "degraded",
                                "detail": f"连接中断,看门狗拉起也失败:{result.detail}"}

    def _advance_boot_window_if_pending(self) -> None:
        if self._boot_pending:
            self.main_window.load_url(APP_URL)
            self._boot_pending = False

    def _heartbeat_loop(self) -> None:
        while True:
            if self._retry_requested.wait(timeout=_HEARTBEAT_SECONDS):
                self._retry_requested.clear()
            self.heartbeat_once()

    def _startup(self) -> None:
        self.run_boot_sequence(self.main_window)
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def start(self) -> None:
        self._wv.start(self._startup, private_mode=False)


def _eval(win, code: str) -> None:
    """evaluate_js 在窗口关闭竞态里会抛;壳绝不因为一句注入而崩 —— 但要留痕。

    pywebview 自己那条 WebViewException/JavascriptException 是记到一个哪儿都不去
    的 logger 上的(pythonw 下没有控制台接它),照抄同款"吞掉"只会让 overlay.js
    注入失败这种事彻底没人知道。
    """
    try:
        win.evaluate_js(code)
    except Exception as exc:  # noqa: BLE001
        _log_shell_event(f"evaluate_js failed: {type(exc).__name__}: {exc}")


def _log_shell_event(message: str) -> None:
    try:
        _SHELL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _SHELL_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {message}\n")
    except Exception:  # noqa: BLE001 —— 连日志都写不进去也不能让壳崩
        pass


def create_shell(*, webview_module, ensure: Callable | None = None,
                 prober: Callable | None = None, spawner: Callable | None = None,
                 contamination: Callable | None = None,
                 file_opener: Callable[[str], None] | None = None) -> Shell:
    prober = prober or sv.probe
    spawner = spawner or sv.spawn_watchdog
    return Shell(
        webview_module=webview_module,
        # 默认 ensure 必须真的用上面这两个(可能被注入的)prober/spawner,不能让
        # sv.ensure_running 悄悄回落到它自己的模块级默认值 —— 否则谁只注入了
        # prober/spawner 却没管 ensure,测试会静默打到真实网络上。
        ensure=ensure or (lambda **kw: sv.ensure_running(prober=prober, spawner=spawner, **kw)),
        prober=prober,
        spawner=spawner,
        contamination=contamination or sv.port_contamination,
        file_opener=file_opener or os.startfile,
    )
