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
import time
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
_LOG_DEDUPE_SECONDS = 60.0
_last_log_at: dict[str, float] = {}


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
        # run_boot_sequence 最后一次真正推给引导页的完整 state —— 重试反馈要在
        # 这份状态上只换 message,不能整个覆盖(不然 phase/detail/warning 全丢)。
        self._last_boot_state: dict | None = None
        # 每完成一次 degraded→healthy 的恢复就加一;_on_loaded 用它判断"我读到
        # degraded 的时候,恢复是不是已经在我背后发生了"——见 _maybe_show_overlay_for_late_load。
        self._connection_generation = 0
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
        # 捕获"此刻"的连接代数,交给 _maybe_show_overlay_for_late_load 在真正
        # 决定要不要发 show 之前再核对一遍 —— 见该方法的说明。
        self._maybe_show_overlay_for_late_load(win, self._connection_generation)

    def _maybe_show_overlay_for_late_load(self, win, generation_at_read: int) -> None:
        """这扇窗刚加载完;如果此刻仍是掉线状态,且这段时间里没有发生一次
        degraded→healthy 的恢复(用 _connection_generation 判断),才补一次 show
        —— 这扇窗是在服务器已经掉线期间才打开的,上一次 show 只广播给了当时已经
        存在的窗口,不补这一手,用户会看到一扇"看起来正常"的假窗口。

        代数比较紧跟在状态判断之后、就在进入 _eval 之前 —— 这是为了把竞态窗口从
        "整个 _eval 往返的 IPC 耗时"收窄到"这一次判断和紧跟着的 _eval 调用之间"
        这一条 Python 语句的距离:如果 _on_loaded 捕获代数之后、真正调用这个
        show eval 之前,heartbeat 已经抢先完成了一次恢复(代数已经变了),这次
        show 就必须被跳过,否则它会晚于 heartbeat 那次(现在恢复成只发一次的)
        hide 落地,留下一层再也没人揭的幕布。
        """
        if self._status.get("state") == "degraded" and self._connection_generation == generation_at_read:
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
            _navigate(win, APP_URL)
            self._boot_pending = False
            return
        self._push_boot(win, {"phase": outcome.state, "message": "启动失败",
                              "detail": outcome.detail, "warning": warning})

    def _push_boot(self, win, state: dict) -> None:
        self._last_boot_state = state
        _eval(win, f"window.glBoot && window.glBoot.setState({json.dumps(state, ensure_ascii=False)});")

    def _on_retry(self) -> None:
        self._retry_requested.set()
        if self._boot_pending and self._last_boot_state is not None:
            # 点了以前只是悄悄唤醒心跳提前探一次,页面上什么反应都没有 ——
            # 第一次点和以前那个死气沉沉的按钮长得一模一样。这里立刻推一条
            # 反馈上去,不等心跳线程真正跑完那一轮探测。
            #
            # 只换 message,phase/detail/warning 原样保留 —— boot.html 的
            # setState 是无条件覆盖式的:phase 一旦被换成不代表"卡住"的值,
            # stuck 就会算成 false,重试/看日志两个按钮直接消失,GUANLAN_PORT
            # 污染警告也没有任何东西会再推一次完整状态回去、永久消失。
            retry_state = dict(self._last_boot_state)
            retry_state["message"] = "正在重试…"
            self._push_boot(self.main_window, retry_state)

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
                # 每完成一次 degraded→healthy 的恢复就把连接代数加一 ——
                # _on_loaded 用它判断"我读到 degraded 的那一刻,恢复是不是已经
                # 在我背后发生了",从而跳过一次本该被这次 hide 盖掉的迟到 show。
                # 见 _maybe_show_overlay_for_late_load 的说明。
                self._connection_generation += 1
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
            _navigate(self.main_window, APP_URL)
            self._boot_pending = False

    def _protected_heartbeat_tick(self) -> None:
        """一次心跳的异常绝不能让往后所有的心跳都消失 —— 这仓库最惨的一次停机
        就是同一种「监督链悄悄死掉,没人发现」。"""
        try:
            self.heartbeat_once()
        except Exception as exc:  # noqa: BLE001
            _log_shell_event(f"heartbeat_once failed: {type(exc).__name__}: {exc}")

    def _heartbeat_loop(self) -> None:
        while True:
            if self._retry_requested.wait(timeout=_HEARTBEAT_SECONDS):
                self._retry_requested.clear()
            self._protected_heartbeat_tick()

    def _startup(self) -> None:
        try:
            self.run_boot_sequence(self.main_window)
        except Exception as exc:  # noqa: BLE001 —— 这异常死在 pywebview 生的线程上,
            # main() 的 _run_and_log_crashes 看不到;必须自己留痕,而且无论如何都要
            # 把心跳线程起起来 —— 否则引导页会经这第二条路再次原地卡死,悄无声息。
            _log_shell_event(f"run_boot_sequence failed: {type(exc).__name__}: {exc}")
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


def _navigate(win, url: str) -> None:
    """load_url 是壳里唯一没走 _eval 式留痕的 pywebview 调用。真 pywebview 的
    `load_url` 装了 `@_shown_call`,一扇从没 show 过的窗口(WebView2 运行时坏掉、
    GPU 挂起)会等 20s 再抛 WebViewException;导航一个已关闭的窗口则是良性的
    静默 no-op(winforms 那边实例没了直接提前返回,不会抛)。壳绝不能因为一次
    导航失败就把 run_boot_sequence/心跳线程带崩,但要留痕 —— 同 _eval。
    """
    try:
        win.load_url(url)
    except Exception as exc:  # noqa: BLE001
        _log_shell_event(f"load_url failed: {type(exc).__name__}: {exc}")


def _log_shell_event(message: str) -> None:
    """诊断日志,不能无界增长:同一条消息在 `_LOG_DEDUPE_SECONDS` 窗口内只写一次
    —— 一扇卡在永久失败态的窗口每一拍心跳都会炸出同一条 evaluate_js 失败,
    不限流就是一天约 8600 行、永远写下去。这是诊断用途,限流丢的是重复,不丢
    首次出现。
    """
    now = time.monotonic()
    last = _last_log_at.get(message)
    if last is not None and (now - last) < _LOG_DEDUPE_SECONDS:
        return
    # 去重表自己不能是那个"无界增长"问题的一个更小翻版:把窗口之外的旧键清掉,
    # 让这张表始终只装着"最近一个去重窗口内出现过的不同消息",而不是从进程
    # 启动那一刻起见过的所有消息。
    for key in [k for k, at in _last_log_at.items() if (now - at) >= _LOG_DEDUPE_SECONDS]:
        del _last_log_at[key]
    _last_log_at[message] = now
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
