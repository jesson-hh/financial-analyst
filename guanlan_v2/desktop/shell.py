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
# webview.start() 不传 storage_path 时,private_mode=False 下 pywebview 会把
# profile 放进 %APPDATA%/pywebview —— 所有没设 storage_path 的 pywebview 应用
# 共享的默认目录。Task 7 验收时观测到这台机器上一个不相关的 pywebview 应用
# (G:\stocks\dist\GuanlanDataManager\GuanlanDataManager.exe)同样没设它,我们的
# 渲染器因此接上了对方已经在跑的浏览器 broker 进程而不是拿到独立的一份——
# WebView2 一个 user-data 目录只有一个浏览器 broker 进程,对方崩溃/强制更新/
# 非正常关闭会连带拖垮我们的渲染器(反之亦然),localhost 的 cookie 也不按端口
# 区分(RFC 6265),两个不相关应用之间会串。给自己一个 app 专属目录,消掉这份
# 耦合。
#
# 故意不放进仓库的 var/ ——那是 _LOG_PATH/_SHELL_LOG_PATH 那种一次性诊断产物的
# 地盘,这仓库出现过 `git clean -fdx` 把 gitignored 内容整个清掉的先例;浏览器
# profile 是反过来的东西:是要长期保留的用户状态,而且是几十到几百 MB 的二进制
# 缓存,放进工作树只会让每一次 --no-ignore 搜索/备份遍历都要扫过它。落在
# %LOCALAPPDATA% 下(变量缺失时才退回仓库 var/,保证任何机器都能跑起来)。
_WEBVIEW_STORAGE_PATH = (
    Path(os.environ.get("LOCALAPPDATA") or (_DIR.parents[1] / "var"))
    / "Guanlan" / "webview2-profile"
)
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
        # show 广播和 hide 广播共用的闩:True 表示"当前有一层遮罩是通过广播
        # 显示的、将来一定会有对应的一次 hide 广播"。只有这把闩为真时,
        # late-load 才可以补一次 show —— 不能只看 self._status(探测级信号,
        # 第 1 次失败就已经是 degraded),否则会在闩还没锁上(ConnectionMonitor
        # 要连续 3 次失败才锁)的时候画一层将来没有任何 hide 会去揭的幕布。
        self._overlay_broadcast = False
        # 只保护 (_connection_generation, _overlay_broadcast) 这一对状态的读写
        # 保持原子、不撕裂 —— 不覆盖 evaluate_js 调用本身。真 pywebview 的
        # evaluate_js 装了 _pywebview_ready_call,对一扇还没 show 过的窗口会等
        # 满 20s 才抛;如果把这个锁的范围伸到 eval 调用上,心跳线程可能因为
        # 广播列表里有一扇还没就绪的窗口而扛着锁等满 20s,这段时间里会连带
        # 拖住任何其它窗口的 _on_loaded 决策——那正是"心跳线程等 GUI 线程"的
        # 那类阻塞,详见 _maybe_show_overlay_for_late_load 和 heartbeat_once
        # 里的说明与任务报告。
        self._overlay_lock = threading.Lock()
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
        except Exception as exc:  # noqa: BLE001 —— 事件挂不上不该拖垮建窗,但要留痕:
            # 这一挂失败,window.GL_DESKTOP 和 overlay.js 就再也不会被注入到这扇
            # 窗口的任何一次导航里,掉线遮罩也再不会在它身上出现——这个模块里
            # 唯一一处"后果很大、此前完全不留痕"的吞异常。
            _log_shell_event(f"loaded event binding failed: {type(exc).__name__}: {exc}")
        try:
            win.events.closed += lambda: self._on_closed(win)
        except Exception as exc:  # noqa: BLE001 —— 同上,留痕
            _log_shell_event(f"closed event binding failed: {type(exc).__name__}: {exc}")
        return win

    def open_ui_window(self, url: str) -> None:
        """给 bridge 用的窗口工厂。URL 已被 bridge 的安全闸放行过。

        只能在非 MainThread 上调用:真 pywebview 的 `webview.create_window`
        只在 `threading.current_thread().name != 'MainThread'` 时才真的创建
        窗口(`__init__.py:417-427`);在 MainThread 上它只会往
        `webview.windows` 追加一个从未初始化、从未 show 过的 Window 就返回。
        JsApi 的调用天然经由 pywebview 自己派生的 `Thread-N`
        (`util.js_bridge_call`)到达这里,所以今天始终踩在对的一侧——但如果
        以后有人把这个方法接到原生菜单或 GUI 线程回调上,后果是这个模块历史
        上最惨的状态:一扇永不 show 的窗口,`closed` 永不触发(`_on_closed`
        永远剔不掉它),它的 `evaluate_js` 会在之后每一拍心跳上白等 20s,拖垮
        整条心跳线程。
        """
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
        # GL_DESKTOP 是留给将来换壳的可移植标记 —— **顶栏并不读它**。
        # guanlan-nav.js 只认一个信号:可调用的 window.pywebview.api.open_window。
        # 理由是一个没有桥在后面的标志位开不出窗口,而 pywebview 自己注入的 api 对象
        # 既是真正必需的东西、又与顶栏 parse 时执行的 IIFE 无竞态。这里仍然注入它,
        # 是为了将来换掉 pywebview 时前端不必改;别把这行读成顶栏的依赖。
        _eval(win, "window.GL_DESKTOP = true;")
        _eval(win, _OVERLAY_JS)
        # 捕获"此刻"的连接代数,交给 _maybe_show_overlay_for_late_load 在真正
        # 决定要不要发 show 之前再核对一遍 —— 见该方法的说明。
        self._maybe_show_overlay_for_late_load(win, self._connection_generation)

    def _maybe_show_overlay_for_late_load(self, win, generation_at_read: int) -> None:
        """这扇窗刚加载完;只有在"当前确实有一层遮罩是被广播出去的、将来一定会有
        对应的一次 hide 广播"(`_overlay_broadcast`)且这段时间里没有发生一次
        degraded→healthy 的恢复(`_connection_generation` 判断)时,才补一次 show
        —— 这扇窗是在服务器已经掉线期间才打开的,上一次 show 只广播给了当时已经
        存在的窗口,不补这一手,用户会看到一扇"看起来正常"的假窗口。

        判断条件必须是 `_overlay_broadcast`,不能是 `self._status`:后者是探测级
        信号,heartbeat_once 在**第 1 次**探测失败就已经把它标成 degraded(round 2
        的有意设计,是为了让 server_status() 保持新鲜);而 show/hide 广播、以及
        连接代数的递增,都只在 ConnectionMonitor 的闩被锁上时才发生 —— 那要连续
        **第 3 次**失败才会。第 1、2 次失败时如果只看 _status,late-load 会画上
        一层帷幄,而恢复时 hide_overlay 因为闩从没锁过而恒为 False,没有任何东西
        会去揭它 —— 和竞态版本一样的"永久幕布",只是完全不需要任何交错就能触发,
        门槛低得多。`_overlay_broadcast` 和 show/hide 广播共用同一把闩,所以只要
        它为真,将来就保证有一次 hide 会把它清掉。

        (`_connection_generation`, `_overlay_broadcast`) 这一对状态的读取经
        `_overlay_lock` 保持原子,不会撕裂读到"代数已经变了但广播位还没清"这种
        不一致组合;但锁只护住这次读取,不覆盖下面这句 _eval —— 见 __init__ 里
        对这个决定的说明和任务报告里的取舍。
        """
        with self._overlay_lock:
            should_show = self._overlay_broadcast and self._connection_generation == generation_at_read
        if should_show:
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
            # 只有导航真的成功才清 _boot_pending —— 否则一次失败的导航
            # (WebView2 运行时坏掉/GPU 挂起,_navigate 20s 后吞掉并留痕)会
            # 把这个唯一会重试导航的开关永久关掉:引导页停在原地,以后
            # 再也没有任何代码路径会去调 load_url。留 _boot_pending 为 True,
            # 下一拍心跳的 _advance_boot_window_if_pending 会自动重试。
            if _navigate(win, APP_URL):
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
        """用系统默认程序打开 var/server-9999.log。只读,不动服务器。

        JsApi.open_log 已经会把这里抛出的异常包成 {ok: False, detail: ...}
        还给网页,但 JsApi 故意不 import shell、没有留痕能力(见其模块文档)——
        如果不在这里也记一笔,`os.startfile` 失败(没有 .log 关联程序/文件被
        删/漫游 profile)时 看日志 按钮就会在 UI 上和 Python 侧同时哑掉,一丝
        痕迹都留不下。留痕后原样重新抛出,让 JsApi 那层已有的失败语义不变。
        """
        try:
            self._file_opener(str(_LOG_PATH))
        except Exception as exc:  # noqa: BLE001
            _log_shell_event(f"open_log failed: {type(exc).__name__}: {exc}")
            raise

    # ── 心跳 ────────────────────────────────────────────────────────
    def heartbeat_once(self) -> None:
        decision = self._monitor.observe(self._probe())
        if decision.connected:
            # status 必须每次探测都刷新,不能只在"进入/离开遮罩"的跳变沿刷新 ——
            # 否则超时之后的引导页会让 server_status() 永远读到 "timeout"
            # (监测器压根没见过那次超时,recover 时 hide_overlay 也不会是 True)。
            self._status = {"state": "healthy", "detail": ""}
            if decision.hide_overlay:
                # 每完成一次 degraded→healthy 的恢复就把连接代数加一、清掉广播闩
                # —— 两者一起经 _overlay_lock 原子更新,_on_loaded 用它们判断
                # "我读到的时候,恢复是不是已经在我背后发生了",从而跳过一次
                # 本该被这次 hide 盖掉的迟到 show。见 _maybe_show_overlay_for_late_load。
                with self._overlay_lock:
                    self._connection_generation += 1
                    self._overlay_broadcast = False
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
            # 这里(闩刚锁上的那一刻)才把广播闩置真 —— 在此之前(第 1、2 次
            # 失败)_status 已经是 degraded,但闩没锁,late-load 不该显示任何
            # 东西,因为将来恢复时不保证有一次 hide 广播去揭它。
            with self._overlay_lock:
                self._overlay_broadcast = True
            for win in list(self._windows):
                _eval(win, "window.glShellOverlay && window.glShellOverlay.show('正在等待服务器恢复…');")
            result = self._spawn()      # 一次掉线只拉一次 —— 由 ConnectionMonitor 保证
            if not result.ok:
                self._status = {"state": "degraded",
                                "detail": f"连接中断,看门狗拉起也失败:{result.detail}"}

    def _advance_boot_window_if_pending(self) -> None:
        if self._boot_pending:
            # 同 run_boot_sequence 那处:只有导航真的成功才清 _boot_pending,
            # 失败就留着,让下一拍心跳再试一次。
            if _navigate(self.main_window, APP_URL):
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
        # storage_path 显式指向 %LOCALAPPDATA%\Guanlan\webview2-profile(变量缺失
        # 时退回仓库 var/)—— 见上面 _WEBVIEW_STORAGE_PATH 的注释:不传就会落回
        # 跨应用共享的默认 profile 目录。private_mode 保持 False 不变(这份
        # profile 就是要跨进程重启持久化,不是每次清空的临时区)—— UserDataFolder
        # 和 IsInPrivateModeEnabled 是 WebView2 上两个独立的属性
        # (edgechromium.py 的 EdgeChrome.__init__),换个位置不会波及是否持久化。
        #
        # 【round 2 更正】这里原来写的理由是"pywebview 自己的 makedirs 兜底
        # 会静默失败,我们要抢在它前面"——那个说法只对**默认**的
        # %APPDATA%/pywebview 路径成立。一旦像我们这样显式传了 storage_path,
        # webview.start() 会先调 __set_storage_path(webview/__init__.py:
        # 487-498):它自己 makedirs 一次、再 os.access(..., W_OK) 校验,任何
        # 一步失败都会 raise WebViewException,这个异常会一路传到
        # _run_and_log_crashes、留下完整 traceback——这条路径其实已经被
        # pywebview 自己稳稳接住了,不需要我们抢跑。
        # 这里预建目录真正补上的缺口是**路径被一个文件占用**这一种:那种情况下
        # __set_storage_path 看到 os.path.exists() 为真、os.access(file, W_OK)
        # 对一个可写文件同样为真,于是原样把这个文件路径设成 storage_path,
        # 不检查它是不是目录——winforms.init_storage 跟着也不会再 makedirs
        # 一次,WebView2 拿到一个指向文件的 UserDataFolder,后果是一扇什么都
        # 不渲染的窗口,var/desktop-shell.log 和崩溃日志里都不会有任何一行
        # 痕迹。我们自己的 Path.mkdir(exist_ok=True) 在路径已被文件占用时依然
        # 会抛(CPython:exist_ok 只兜"已经是目录"这一种,不兜"已经是别的
        # 东西"),正好在这里接住、留痕。磁盘满/权限问题这些情况 pywebview
        # 自己也会抛,我们这里的 try 只是先一步、多一行诊断,不冲突。
        try:
            _WEBVIEW_STORAGE_PATH.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            _log_shell_event(f"webview storage path unusable: {type(exc).__name__}: {exc}")
        self._wv.start(self._startup, private_mode=False,
                       storage_path=str(_WEBVIEW_STORAGE_PATH))


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


def _navigate(win, url: str) -> bool:
    """load_url 是壳里唯一没走 _eval 式留痕的 pywebview 调用。真 pywebview 的
    `load_url` 装了 `@_shown_call`,一扇从没 show 过的窗口(WebView2 运行时坏掉、
    GPU 挂起)会等 20s 再抛 WebViewException;导航一个已关闭的窗口则是良性的
    静默 no-op(winforms 那边实例没了直接提前返回,不会抛)。壳绝不能因为一次
    导航失败就把 run_boot_sequence/心跳线程带崩,但要留痕 —— 同 _eval。

    返回是否真的成功:调用方(run_boot_sequence / _advance_boot_window_if_pending)
    只应该在导航成功时才把 _boot_pending 清掉——否则一次吞掉的失败会让
    "重试导航"这件事的唯一开关被永久关闭,引导页原地卡死,没有任何东西会
    再尝试导航。
    """
    try:
        win.load_url(url)
        return True
    except Exception as exc:  # noqa: BLE001
        _log_shell_event(f"load_url failed: {type(exc).__name__}: {exc}")
        return False


def _log_shell_event(message: str) -> None:
    """诊断日志,不能无界增长:同一条消息在 `_LOG_DEDUPE_SECONDS` 窗口内只写一次
    —— 一扇卡在永久失败态的窗口每一拍心跳都会炸出同一条 evaluate_js 失败,
    不限流就是一天约 8600 行、永远写下去。这是诊断用途,限流丢的是重复,不丢
    首次出现。

    整个函数体在**一个**try 里:`_last_log_at` 被好几个线程无锁地读写(心跳
    线程、每扇窗口各自的 loaded 事件线程、js-bridge 线程),清扫循环遍历
    `.items()` 时如果另一个线程恰好插入了新键,CPython 会抛
    `RuntimeError: dictionary changed size during iteration`。这个函数唯一
    的调用方是各处的 except 块 —— 这条异常如果从这里逃出去,对调用方来说就是
    "except 分支里又抛了一次",而同一个 try 接不住自己 except 块里抛出来的
    异常,对 `_protected_heartbeat_tick` 来说这会直接穿透它自己的 try、经
    `_heartbeat_loop` 的 `while True` 把心跳线程带走 —— 正是本函数存在的
    理由(不让心跳线程死于一次异常)被它自己的新逻辑重新捅开一个洞。删除清扫
    循环换来的确定性不值当,所以这里选择把它纳入同一个 try,宁可偶尔丢一次
    去重表维护也不能让这个"绝不能抛"的函数真的抛出去。
    """
    try:
        now = time.monotonic()
        last = _last_log_at.get(message)
        if last is not None and (now - last) < _LOG_DEDUPE_SECONDS:
            return
        # 去重表自己不能是那个"无界增长"问题的一个更小翻版:把窗口之外的旧键
        # 清掉,让这张表始终只装着"最近一个去重窗口内出现过的不同消息",而不是
        # 从进程启动那一刻起见过的所有消息。
        for key in [k for k, at in _last_log_at.items() if (now - at) >= _LOG_DEDUPE_SECONDS]:
            del _last_log_at[key]
        _last_log_at[message] = now
        _SHELL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _SHELL_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {message}\n")
    except Exception:  # noqa: BLE001 —— 连日志/去重表本身出问题也不能让壳崩
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
