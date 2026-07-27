# -*- coding: utf-8 -*-
"""9999 的判活与拉起。无 GUI、仅 stdlib,故可无头测试。

红线:本模块**只启动看门狗代际**,永远不杀进程、不绑端口、不停服务。
看门狗(scripts/check_9999.ps1)仍是 9999 的唯一业主;壳只是又一个「发现它
死了就拉一把」的触发源,与 guanlan_v2/server.py 的 _checker_revive_loop 同构。
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WATCHDOG_SCRIPT = _REPO_ROOT / "scripts" / "check_9999.ps1"

PORT = 9999
HEALTH_URL = f"http://127.0.0.1:{PORT}/workflow/list"
APP_URL = f"http://127.0.0.1:{PORT}/ui/console/%E8%A7%82%E6%BE%9C%20%C2%B7%20%E5%B8%B7%E5%B9%84.html"

# 与 guanlan_v2/server.py 的 _checker_revive_loop 同一条命令行与同一组 flags。
# 本机 Task Scheduler 派生的进程会冻死(见 memory watchdog-9999 大坑①),故不用 schtasks。
_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def probe(*, timeout: float = 5.0, opener: Callable = urllib.request.urlopen) -> bool:
    """9999 现在应答吗?与看门狗用同一个健康端点。任何异常都算不健康。"""
    try:
        with opener(HEALTH_URL, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0)) == 200
    except Exception:  # noqa: BLE001 —— 判活不该抛,不通就是不通
        return False


def watchdog_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """给看门狗用的环境:**剔除 GUANLAN_PORT**。

    2026-07-19~07-26 停机 7.5 天的根因就是这个变量漏进代际链、一路继承,
    害得每一代拉起的 server 都去 bind 9998,9999 永远没有监听。壳是一个新的
    派生点,不能再开同样的口子。
    """
    env = dict(os.environ if base is None else base)
    env.pop("GUANLAN_PORT", None)
    return env


@dataclass(frozen=True)
class SpawnResult:
    ok: bool
    pid: int | None
    detail: str


def spawn_watchdog(*, popen: Callable = subprocess.Popen,
                   base_env: Mapping[str, str] | None = None) -> SpawnResult:
    """拉起一代看门狗。只启动,不杀任何东西。"""
    cmd = [
        "C:\\Windows\\System32\\conhost.exe", "--headless", "powershell.exe",
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_WATCHDOG_SCRIPT),
    ]
    try:
        proc = popen(cmd, creationflags=_DETACHED, cwd=str(_REPO_ROOT),
                     env=watchdog_env(base_env))
    except Exception as exc:  # noqa: BLE001
        return SpawnResult(False, None, f"{type(exc).__name__}: {exc}")
    return SpawnResult(True, getattr(proc, "pid", None), "watchdog generation spawned")


@dataclass(frozen=True)
class EnsureOutcome:
    state: str          # "healthy" | "timeout" | "spawn_failed"
    spawned: bool
    waited_seconds: float
    detail: str


def ensure_running(*, deadline_seconds: float = 90.0, poll_seconds: float = 2.0,
                   prober: Callable = probe, spawner: Callable = spawn_watchdog,
                   sleep: Callable = time.sleep, clock: Callable = time.monotonic,
                   on_progress: Callable[[str], None] | None = None) -> EnsureOutcome:
    """探活;不通就拉一代看门狗,然后轮询到健康或超时。看门狗只拉一次。"""
    def _say(msg: str) -> None:
        if on_progress is not None:
            on_progress(msg)

    started = clock()
    _say("正在检查 9999")
    if prober():
        return EnsureOutcome("healthy", False, 0.0, "服务器已在运行")

    _say("9999 未监听,正在拉起看门狗")
    spawn = spawner()
    if not spawn.ok:
        return EnsureOutcome("spawn_failed", False, clock() - started, spawn.detail)

    while True:
        waited = clock() - started
        if waited >= deadline_seconds:
            return EnsureOutcome("timeout", True, waited,
                                 f"看门狗已拉起,但 {deadline_seconds:.0f}s 内 9999 仍未监听")
        _say(f"等待服务器启动 {waited:.0f}s")
        sleep(poll_seconds)
        if prober():
            return EnsureOutcome("healthy", True, clock() - started, "服务器已拉起")


def port_contamination() -> str | None:
    """壳自己的环境里有非 9999 的 GUANLAN_PORT 吗?有就该在引导页上诚实显形。"""
    raw = os.environ.get("GUANLAN_PORT")
    if raw and raw.strip() != str(PORT):
        return f"检测到 GUANLAN_PORT={raw}(壳仍连 {PORT})"
    return None


@dataclass(frozen=True)
class MonitorDecision:
    connected: bool
    show_overlay: bool
    hide_overlay: bool
    spawn_watchdog: bool
    consecutive_failures: int


class ConnectionMonitor:
    """把一串心跳结果翻译成动作。纯状态机,不做 I/O。

    硬约束:一次掉线只拉一次看门狗,按壳进程计不按窗口计 —— 开着三个窗口
    掉线仍然只派生一次。恢复健康后标记复位,下一次掉线才允许再拉一次。
    """

    def __init__(self, *, failure_threshold: int = 3) -> None:
        self._threshold = failure_threshold
        self._failures = 0
        self._degraded = False

    def observe(self, healthy: bool) -> MonitorDecision:
        if healthy:
            was_degraded = self._degraded
            self._failures = 0
            self._degraded = False
            return MonitorDecision(True, False, was_degraded, False, 0)

        self._failures += 1
        entering = (not self._degraded) and self._failures >= self._threshold
        if entering:
            self._degraded = True
        return MonitorDecision(False, entering, False, entering, self._failures)
