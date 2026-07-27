# -*- coding: utf-8 -*-
"""pywebview 接线。真实逻辑在 supervisor 与 bridge,这里只负责把它们连到窗口上。

webview 模块以参数注入(而不是模块层 import),所以接线本身可以用假模块测试,
测试机也不需要 GUI。
"""
from __future__ import annotations

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


class Shell:
    def __init__(self, *, webview_module, ensure: Callable, prober: Callable,
                 spawner: Callable, contamination: Callable) -> None:
        self._wv = webview_module
        self._ensure = ensure
        self._probe = prober
        self._spawn = spawner
        self._contamination = contamination
        self._monitor = sv.ConnectionMonitor()
        self._windows: list = []
        self._status: dict = {"state": "checking", "detail": ""}
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
            win.events.loaded += lambda w=win: self._on_loaded(w)
        except Exception:  # noqa: BLE001 —— 事件挂不上不该拖垮建窗
            pass
        return win

    def open_ui_window(self, url: str) -> None:
        """给 bridge 用的窗口工厂。URL 已被 bridge 的安全闸放行过。"""
        self._new_window(url, WINDOW_TITLE)

    def _on_loaded(self, win) -> None:
        # GL_DESKTOP 是给顶栏的可移植信号;顶栏同时也认 window.pywebview,故无竞态。
        _eval(win, "window.GL_DESKTOP = true;")
        _eval(win, _OVERLAY_JS)

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
            return
        self._push_boot(win, {"phase": outcome.state, "message": "启动失败",
                              "detail": outcome.detail, "warning": warning})

    def _push_boot(self, win, state: dict) -> None:
        _eval(win, f"window.glBoot && window.glBoot.setState({json.dumps(state, ensure_ascii=False)});")

    def _on_retry(self) -> None:
        self._retry_requested.set()

    def _on_open_log(self) -> None:
        """用系统默认程序打开 var/server-9999.log。只读,不动服务器。"""
        os.startfile(str(_LOG_PATH))  # noqa: S606 —— Windows-only,路径是常量

    # ── 心跳 ────────────────────────────────────────────────────────
    def heartbeat_once(self) -> None:
        decision = self._monitor.observe(self._probe())
        if decision.show_overlay:
            self._status = {"state": "degraded", "detail": "连接中断"}
            for win in list(self._windows):
                _eval(win, "window.glShellOverlay && window.glShellOverlay.show('正在等待服务器恢复…');")
            self._spawn()          # 一次掉线只拉一次 —— 由 ConnectionMonitor 保证
        elif decision.hide_overlay:
            self._status = {"state": "healthy", "detail": ""}
            for win in list(self._windows):
                _eval(win, "window.glShellOverlay && window.glShellOverlay.hide();")

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
    """evaluate_js 在窗口关闭竞态里会抛;壳绝不因为一句注入而崩。"""
    try:
        win.evaluate_js(code)
    except Exception:  # noqa: BLE001
        pass


def create_shell(*, webview_module, ensure: Callable | None = None,
                 prober: Callable | None = None, spawner: Callable | None = None,
                 contamination: Callable | None = None) -> Shell:
    return Shell(
        webview_module=webview_module,
        ensure=ensure or (lambda **kw: sv.ensure_running(**kw)),
        prober=prober or sv.probe,
        spawner=spawner or sv.spawn_watchdog,
        contamination=contamination or sv.port_contamination,
    )
