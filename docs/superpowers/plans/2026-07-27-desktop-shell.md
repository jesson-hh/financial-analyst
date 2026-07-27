# 观澜桌面壳 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 双击一个图标打开一个独立的观澜窗口,它自己确保 9999 活着,并支持 Ctrl+点顶栏开新窗。

**Architecture:** 新包 `guanlan_v2/desktop/`。全部真实逻辑放进两个**不 import pywebview** 的模块(`supervisor.py` 判活与拉起、`bridge.py` 网页可调的 API 与安全闸),GUI 能力一律以可调用对象注入,因此可无头测试。`shell.py` 只做 pywebview 接线。现有前端只动 `ui/_shared/guanlan-nav.js` 一个文件,新增分支在浏览器里完全不生效。

**Tech Stack:** Python 3.13 + pywebview 6.2.1(WebView2 后端)+ pythonnet;pytest;PowerShell 5.1(快捷方式安装脚本)。

**Design spec:** `docs/superpowers/specs/2026-07-27-desktop-shell-design.md`

## Global Constraints

- **仓根**:`G:\guanlan-v2`。**venv**:`G:\financial-analyst\.venv\Scripts\python.exe`。跑测试一律用这个解释器。
- **端口固定 9999。** 壳绝不读 `GUANLAN_PORT` 来决定连哪个端口。
- **看门狗是 9999 的唯一业主。** 壳只 *启动* 看门狗代际;**任何代码路径都不得杀进程、不得绑定端口、不得停服务**。
- **派生看门狗时必须显式剔除 `GUANLAN_PORT`**(2026-07-19~07-26 那次 7.5 天停机的根因)。
- **启动形态是 `-m`**,不是脚本路径(2026-07-26 那次 9999 起不来的根因;守卫见 `tests/test_server_script_launch.py`)。
- **不改这些**:`engine/**`、除 `ui/_shared/guanlan-nav.js` 外的 `ui/**`、`guanlan_v2/server.py`。
- **提交只用显式 pathspec。永远不要 `git add -A` 或 `git add .`** —— 另一个并发 session 持有 `guanlan_v2/console/`、`guanlan_v2/datafeed/`、`guanlan_v2/glmcp/`、`ui/screen/`、`docs/README.md`、`.data/` 下的未提交改动。误暂存属重大事故。
- **绝不读写 `var/secrets.env`。**
- **运维 ps1 注释一律 ASCII**(PS 5.1 会把无 BOM UTF-8 的非 ASCII 字节当 ANSI 读,语法炸)。
- 所有新 Python 文件带 `# -*- coding: utf-8 -*-` 与 `from __future__ import annotations`,与仓内现有模块一致。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `guanlan_v2/desktop/__init__.py` | 包标记。不 import 任何重物。 |
| `guanlan_v2/desktop/supervisor.py` | 探活、派生看门狗、`ensure_running` 启动序列、`ConnectionMonitor` 掉线状态机。**仅 stdlib,无 GUI。** |
| `guanlan_v2/desktop/bridge.py` | `validate_ui_url` 安全闸 + `JsApi`(网页可调)。**无 GUI**,窗口工厂注入。 |
| `guanlan_v2/desktop/boot.html` | 引导 / 错误页。 |
| `guanlan_v2/desktop/overlay.js` | 掉线浮层,壳注入到任意页面。 |
| `guanlan_v2/desktop/shell.py` | pywebview 接线:建窗、注入、心跳循环。 |
| `guanlan_v2/desktop/__main__.py` | 入口 + 仓根引导兜底。 |
| `guanlan_v2/desktop/guanlan.ico` | 图标(一次性生成落盘)。 |
| `scripts/make_desktop_icon.py` | 图标生成器,**只在构建期跑**,pillow 不进运行时依赖。 |
| `scripts/install_desktop_shortcut.ps1` | 幂等生成桌面 + 开始菜单快捷方式。 |
| `ui/_shared/guanlan-nav.js` | **改**:新增桌面分支。 |
| `tests/desktop/` | `__init__.py` + 四个测试文件。 |

---

### Task 1: supervisor —— 探活与拉起

**Files:**
- Create: `guanlan_v2/desktop/__init__.py`
- Create: `guanlan_v2/desktop/supervisor.py`
- Create: `tests/desktop/__init__.py`
- Test: `tests/desktop/test_supervisor.py`

**Interfaces:**
- Consumes: 无(首个任务)
- Produces:
  - `HEALTH_URL: str = "http://127.0.0.1:9999/workflow/list"`
  - `APP_URL: str`(帷幄页完整 URL)
  - `probe(*, timeout: float = 5.0, opener=urllib.request.urlopen) -> bool`
  - `watchdog_env(base: Mapping[str, str] | None = None) -> dict[str, str]`
  - `@dataclass(frozen=True) SpawnResult(ok: bool, pid: int | None, detail: str)`
  - `spawn_watchdog(*, popen=subprocess.Popen, base_env=None) -> SpawnResult`
  - `@dataclass(frozen=True) EnsureOutcome(state: str, spawned: bool, waited_seconds: float, detail: str)` —— `state ∈ {"healthy","timeout","spawn_failed"}`
  - `ensure_running(*, deadline_seconds=90.0, poll_seconds=2.0, prober=probe, spawner=spawn_watchdog, sleep=time.sleep, clock=time.monotonic, on_progress=None) -> EnsureOutcome`
  - `port_contamination() -> str | None`

- [ ] **Step 1: Write the failing tests**

`tests/desktop/__init__.py` 内容为空。`tests/desktop/test_supervisor.py`:

```python
# -*- coding: utf-8 -*-
"""supervisor 的全部行为都在这里定死。这个模块不 import pywebview,故可无头测试。"""
from __future__ import annotations

import pytest

from guanlan_v2.desktop import supervisor as sv


# ── probe ──────────────────────────────────────────────────────────────
def test_probe_true_on_200():
    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert sv.probe(opener=lambda url, timeout: _Resp()) is True


def test_probe_false_on_exception():
    def _boom(url, timeout):
        raise OSError("connection refused")
    assert sv.probe(opener=_boom) is False


def test_probe_false_on_non_200():
    class _Resp:
        status = 503
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert sv.probe(opener=lambda url, timeout: _Resp()) is False


def test_probe_targets_9999_health_endpoint():
    seen = {}
    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def _opener(url, timeout):
        seen["url"] = url
        return _Resp()
    sv.probe(opener=_opener)
    assert seen["url"] == "http://127.0.0.1:9999/workflow/list"


# ── GUANLAN_PORT 剔除:2026-07-19~07-26 停机 7.5 天的根因 ──────────────
def test_watchdog_env_strips_guanlan_port():
    env = sv.watchdog_env({"PATH": "x", "GUANLAN_PORT": "9998", "KEEP": "1"})
    assert "GUANLAN_PORT" not in env
    assert env["PATH"] == "x" and env["KEEP"] == "1"


def test_watchdog_env_does_not_mutate_caller_mapping():
    base = {"GUANLAN_PORT": "9998"}
    sv.watchdog_env(base)
    assert base == {"GUANLAN_PORT": "9998"}


def test_spawn_passes_scrubbed_env_to_popen():
    captured = {}
    class _Proc:
        pid = 4242
    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _Proc()
    r = sv.spawn_watchdog(popen=_popen, base_env={"GUANLAN_PORT": "9998", "PATH": "x"})
    assert r.ok is True and r.pid == 4242
    assert "GUANLAN_PORT" not in captured["kw"]["env"]


def test_spawn_command_line_launches_the_watchdog_script():
    class _Proc:
        pid = 1
    captured = {}
    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc()
    sv.spawn_watchdog(popen=_popen, base_env={})
    joined = " ".join(captured["cmd"])
    assert "check_9999.ps1" in joined
    assert "conhost.exe" in joined and "--headless" in joined


def test_spawn_is_detached():
    class _Proc:
        pid = 1
    captured = {}
    def _popen(cmd, **kw):
        captured["kw"] = kw
        return _Proc()
    sv.spawn_watchdog(popen=_popen, base_env={})
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP —— 与 server.py 的复活循环同口径
    assert captured["kw"]["creationflags"] == 0x00000008 | 0x00000200


def test_spawn_reports_failure_instead_of_raising():
    def _popen(cmd, **kw):
        raise OSError("nope")
    r = sv.spawn_watchdog(popen=_popen, base_env={})
    assert r.ok is False and r.pid is None and "nope" in r.detail


# ── ensure_running ─────────────────────────────────────────────────────
def test_ensure_healthy_on_first_probe_never_spawns():
    def _spawner(**kw):
        raise AssertionError("must not spawn when already healthy")
    out = sv.ensure_running(prober=lambda **kw: True, spawner=_spawner, sleep=lambda s: None)
    assert out.state == "healthy" and out.spawned is False


def test_ensure_spawns_then_succeeds():
    results = iter([False, False, True])
    spawns = []
    out = sv.ensure_running(
        prober=lambda **kw: next(results),
        spawner=lambda **kw: (spawns.append(1), sv.SpawnResult(True, 7, "ok"))[1],
        sleep=lambda s: None,
    )
    assert out.state == "healthy" and out.spawned is True
    assert len(spawns) == 1, "只允许派生一次"


def test_ensure_times_out_and_says_so():
    ticks = iter([0.0, 30.0, 60.0, 95.0])
    out = sv.ensure_running(
        prober=lambda **kw: False,
        spawner=lambda **kw: sv.SpawnResult(True, 7, "ok"),
        sleep=lambda s: None,
        clock=lambda: next(ticks),
        deadline_seconds=90.0,
    )
    assert out.state == "timeout" and out.spawned is True


def test_ensure_reports_spawn_failure_without_waiting():
    out = sv.ensure_running(
        prober=lambda **kw: False,
        spawner=lambda **kw: sv.SpawnResult(False, None, "WMI/Popen boom"),
        sleep=lambda s: None,
    )
    assert out.state == "spawn_failed" and "boom" in out.detail


def test_ensure_emits_progress():
    seen = []
    results = iter([False, True])
    sv.ensure_running(
        prober=lambda **kw: next(results),
        spawner=lambda **kw: sv.SpawnResult(True, 7, "ok"),
        sleep=lambda s: None,
        on_progress=seen.append,
    )
    assert seen, "启动期必须有进度回调,否则引导页只能干等"


# ── 污染警告 ───────────────────────────────────────────────────────────
def test_port_contamination_flags_a_non_9999_value(monkeypatch):
    monkeypatch.setenv("GUANLAN_PORT", "9998")
    assert "9998" in (sv.port_contamination() or "")


def test_port_contamination_silent_when_absent(monkeypatch):
    monkeypatch.delenv("GUANLAN_PORT", raising=False)
    assert sv.port_contamination() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_supervisor.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'guanlan_v2.desktop'`

- [ ] **Step 3: Write the implementation**

`guanlan_v2/desktop/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""观澜桌面壳。

真实逻辑住在 supervisor.py 与 bridge.py —— 两者都不 import pywebview,故可无头测试。
shell.py 只做 GUI 接线。本 __init__ 刻意不 import 任何子模块:导入本包不应把
pywebview 拖进来(测试与 CI 都没有 GUI)。
"""
from __future__ import annotations
```

`guanlan_v2/desktop/supervisor.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_supervisor.py -q`
Expected: PASS,**17 passed**(数字对不上说明有测试被漏写或被静默跳过)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/desktop/__init__.py guanlan_v2/desktop/supervisor.py tests/desktop/__init__.py tests/desktop/test_supervisor.py
git commit -m "feat(desktop): server liveness supervisor that scrubs GUANLAN_PORT"
```

---

### Task 2: bridge —— 网页可调 API 与安全闸

**Files:**
- Create: `guanlan_v2/desktop/bridge.py`
- Test: `tests/desktop/test_bridge.py`

**Interfaces:**
- Consumes: Task 1 的 `supervisor.PORT`
- Produces:
  - `@dataclass(frozen=True) UrlVerdict(ok: bool, reason: str, detail: str)` —— `reason ∈ {"", "not-a-url", "bad-scheme", "bad-host", "bad-port", "not-ui-path"}`
  - `validate_ui_url(raw: str) -> UrlVerdict`
  - `class JsApi` —— `JsApi(*, open_window_factory: Callable[[str], None], status_provider: Callable[[], dict], retry_handler: Callable[[], None], log_opener: Callable[[], None])`,方法 `open_window(url) -> dict`、`server_status() -> dict`、`retry() -> dict`、`open_log() -> dict`

  这**四个**方法就是网页侧 `window.pywebview.api` 的全部面。`boot.html` 的两个按钮调 `retry`/`open_log`,`overlay.js` 的两个按钮也调这两个,顶栏调 `open_window`。改名就是改契约,四处一起改。

- [ ] **Step 1: Write the failing tests**

`tests/desktop/test_bridge.py`:

```python
# -*- coding: utf-8 -*-
"""open_window 的安全闸。

页面里渲染着 RSS 快讯与 LLM 输出 —— 那些内容我们不完全控制。原生壳里一个
不设限的开窗 API 是真实的提权面,所以这里逐条钉死放行与拒绝。
"""
from __future__ import annotations

import pytest

from guanlan_v2.desktop import bridge as br


GOOD = "http://127.0.0.1:9999/ui/screen/x.html"


@pytest.mark.parametrize("url", [
    GOOD,
    "http://127.0.0.1:9999/ui/console/a.html?embed=1",
    "http://127.0.0.1:9999/ui/seats/%E8%A7%82.html",
])
def test_allows_local_ui_pages(url):
    assert br.validate_ui_url(url).ok is True


@pytest.mark.parametrize("url,reason", [
    ("file:///C:/Windows/win.ini", "bad-scheme"),
    ("https://evil.example/ui/x.html", "bad-host"),
    ("http://evil.example/ui/x.html", "bad-host"),
    ("http://127.0.0.1:9998/ui/x.html", "bad-port"),
    ("http://127.0.0.1:9999/api/secret", "not-ui-path"),
    ("http://127.0.0.1:9999/", "not-ui-path"),
    ("http://127.0.0.1:9999/uiX/x.html", "not-ui-path"),
    ("javascript:alert(1)", "bad-scheme"),
    ("", "not-a-url"),
    ("   ", "not-a-url"),
])
def test_rejects_everything_else(url, reason):
    v = br.validate_ui_url(url)
    assert v.ok is False and v.reason == reason


def test_localhost_hostname_is_not_silently_accepted():
    # 只认 127.0.0.1 字面量;放宽会让 host 判断变成一个需要解析的开放问题
    assert br.validate_ui_url("http://localhost:9999/ui/x.html").reason == "bad-host"


# ── JsApi ──────────────────────────────────────────────────────────────
def test_open_window_calls_factory_for_allowed_url():
    opened = []
    api = br.JsApi(open_window_factory=opened.append,
                   status_provider=lambda: {"state": "healthy"},
                   retry_handler=lambda: None)
    out = api.open_window(GOOD)
    assert out["ok"] is True and opened == [GOOD]


def test_open_window_refuses_and_never_calls_factory():
    opened = []
    api = br.JsApi(open_window_factory=opened.append,
                   status_provider=lambda: {"state": "healthy"},
                   retry_handler=lambda: None)
    out = api.open_window("file:///C:/Windows/win.ini")
    assert out["ok"] is False and out["reason"] == "bad-scheme"
    assert opened == [], "被拒的 URL 绝不能碰到窗口工厂"


def test_open_window_survives_a_throwing_factory():
    def _boom(url):
        raise RuntimeError("no window")
    api = br.JsApi(open_window_factory=_boom,
                   status_provider=lambda: {"state": "healthy"},
                   retry_handler=lambda: None)
    out = api.open_window(GOOD)
    assert out["ok"] is False and out["reason"] == "window-failed"


def test_server_status_passes_through():
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {"state": "degraded", "detail": "x"},
                   retry_handler=lambda: None)
    assert api.server_status()["state"] == "degraded"


def test_retry_invokes_handler():
    calls = []
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: calls.append(1),
                   log_opener=lambda: None)
    assert api.retry()["ok"] is True and calls == [1]


def test_open_log_invokes_handler():
    calls = []
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=lambda: calls.append(1))
    assert api.open_log()["ok"] is True and calls == [1]


def test_open_log_survives_a_throwing_handler():
    def _boom():
        raise OSError("no editor")
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=_boom)
    assert api.open_log()["ok"] is False


def test_every_page_facing_method_exists():
    """网页侧只认这四个名字;boot.html / overlay.js / guanlan-nav.js 都硬编码了它们。"""
    for name in ("open_window", "server_status", "retry", "open_log"):
        assert callable(getattr(br.JsApi, name, None)), f"缺 {name}"
```

上面三个已有的 `JsApi(...)` 构造调用同样要补 `log_opener=lambda: None`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_bridge.py -q`
Expected: FAIL —— `ImportError: cannot import name 'bridge'`

- [ ] **Step 3: Write the implementation**

`guanlan_v2/desktop/bridge.py`:

```python
# -*- coding: utf-8 -*-
"""暴露给网页的 API。无 GUID 依赖 —— 窗口工厂由 shell 注入,故可无头测试。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from guanlan_v2.desktop.supervisor import PORT

_ALLOWED_SCHEME = "http"
_ALLOWED_HOST = "127.0.0.1"
_ALLOWED_PREFIX = "/ui/"


@dataclass(frozen=True)
class UrlVerdict:
    ok: bool
    reason: str
    detail: str


def validate_ui_url(raw: str) -> UrlVerdict:
    """只放行本机 9999 上的 /ui/ 页面。其余一律拒绝并说明理由。

    只认 127.0.0.1 字面量,不认 "localhost" —— 放宽会把 host 判断变成一个
    需要名字解析的开放问题,而这是安全边界,不该有开放问题。
    """
    if not raw or not raw.strip():
        return UrlVerdict(False, "not-a-url", "空 URL")
    try:
        parts = urlsplit(raw.strip())
    except ValueError as exc:
        return UrlVerdict(False, "not-a-url", str(exc))
    if parts.scheme != _ALLOWED_SCHEME:
        return UrlVerdict(False, "bad-scheme", f"只允许 {_ALLOWED_SCHEME}:,收到 {parts.scheme or '(空)'}:")
    if parts.hostname != _ALLOWED_HOST:
        return UrlVerdict(False, "bad-host", f"只允许 {_ALLOWED_HOST},收到 {parts.hostname or '(空)'}")
    if (parts.port or 80) != PORT:
        return UrlVerdict(False, "bad-port", f"只允许 {PORT},收到 {parts.port}")
    if not parts.path.startswith(_ALLOWED_PREFIX):
        return UrlVerdict(False, "not-ui-path", f"只允许 {_ALLOWED_PREFIX}* ,收到 {parts.path or '(空)'}")
    return UrlVerdict(True, "", "ok")


class JsApi:
    """pywebview 把本对象的方法挂到网页的 window.pywebview.api 下。

    每个方法都返回 JSON 友好的 dict 且**从不抛异常** —— 异常穿过 JS 桥只会变成
    一个没有信息的 rejected promise,对着页面调试极其难受。
    """

    def __init__(self, *, open_window_factory: Callable[[str], None],
                 status_provider: Callable[[], dict],
                 retry_handler: Callable[[], None],
                 log_opener: Callable[[], None]) -> None:
        self._open_window_factory = open_window_factory
        self._status_provider = status_provider
        self._retry_handler = retry_handler
        self._log_opener = log_opener

    def open_window(self, url: str) -> dict:
        verdict = validate_ui_url(url)
        if not verdict.ok:
            return {"ok": False, "reason": verdict.reason, "detail": verdict.detail}
        try:
            self._open_window_factory(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "window-failed", "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "reason": "", "detail": url}

    def server_status(self) -> dict:
        try:
            return dict(self._status_provider())
        except Exception as exc:  # noqa: BLE001
            return {"state": "unknown", "detail": f"{type(exc).__name__}: {exc}"}

    def retry(self) -> dict:
        try:
            self._retry_handler()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "retry requested"}

    def open_log(self) -> dict:
        try:
            self._log_opener()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "log opened"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_bridge.py -q`
Expected: PASS,**22 passed**(两个 parametrize 展开成 3 + 10)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/desktop/bridge.py tests/desktop/test_bridge.py
git commit -m "feat(desktop): js bridge with a closed allowlist on open_window"
```

---

### Task 3: ConnectionMonitor —— 掉线状态机

**Files:**
- Modify: `guanlan_v2/desktop/supervisor.py`(追加,不改已有代码)
- Test: `tests/desktop/test_connection_monitor.py`

**Interfaces:**
- Consumes: Task 1 的 supervisor 模块
- Produces:
  - `@dataclass(frozen=True) MonitorDecision(connected: bool, show_overlay: bool, hide_overlay: bool, spawn_watchdog: bool, consecutive_failures: int)`
  - `class ConnectionMonitor` —— `ConnectionMonitor(*, failure_threshold: int = 3)`,方法 `observe(healthy: bool) -> MonitorDecision`

- [ ] **Step 1: Write the failing tests**

`tests/desktop/test_connection_monitor.py`:

```python
# -*- coding: utf-8 -*-
"""掉线状态机。

硬约束:**一次掉线只拉一次看门狗 —— 按壳进程计,不按窗口计。** 看门狗自己有
30s 周期;壳再叠一层重试就会变成派生风暴,与 2026-07-20 那个「重启 → 撞端口
→ 死 → 重启」的循环是同一个形状。
"""
from __future__ import annotations

from guanlan_v2.desktop.supervisor import ConnectionMonitor


def test_healthy_stays_quiet():
    m = ConnectionMonitor()
    d = m.observe(True)
    assert d.connected and not d.show_overlay and not d.hide_overlay and not d.spawn_watchdog


def test_failures_below_threshold_do_not_alarm():
    m = ConnectionMonitor(failure_threshold=3)
    assert not m.observe(False).show_overlay
    d = m.observe(False)
    assert not d.show_overlay and d.consecutive_failures == 2


def test_third_failure_shows_overlay_and_spawns_once():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False)
    d = m.observe(False)
    assert d.show_overlay and d.spawn_watchdog and not d.connected


def test_further_failures_never_spawn_again():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False); m.observe(False)
    for _ in range(10):
        d = m.observe(False)
        assert not d.spawn_watchdog, "派生风暴 —— 正是 07-20 那个循环"
        assert not d.show_overlay, "浮层只在进入降级时推一次"


def test_recovery_hides_overlay():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False); m.observe(False)
    d = m.observe(True)
    assert d.connected and d.hide_overlay and d.consecutive_failures == 0


def test_a_second_outage_may_spawn_again():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False); m.observe(False)
    m.observe(True)                       # 恢复 → 标记复位
    m.observe(False); m.observe(False)
    assert m.observe(False).spawn_watchdog is True


def test_blip_below_threshold_leaves_no_trace():
    m = ConnectionMonitor(failure_threshold=3)
    m.observe(False); m.observe(False)
    d = m.observe(True)
    assert not d.hide_overlay, "从没显形过的浮层不该被撤"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_connection_monitor.py -q`
Expected: FAIL —— `ImportError: cannot import name 'ConnectionMonitor'`

- [ ] **Step 3: Write the implementation**

追加到 `guanlan_v2/desktop/supervisor.py` 末尾:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/ -q`
Expected: PASS,**67 passed**(17 + 43 + 7 —— Task 2 的安全闸在两轮修复中从 22 条长到 43 条)

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/desktop/supervisor.py tests/desktop/test_connection_monitor.py
git commit -m "feat(desktop): outage state machine that spawns the watchdog once, not per window"
```

---

### Task 4: boot.html + overlay.js

**Files:**
- Create: `guanlan_v2/desktop/boot.html`
- Create: `guanlan_v2/desktop/overlay.js`
- Test: `tests/desktop/test_assets.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `boot.html` 暴露 `window.glBoot.setState({phase, message, detail, warning})`,`phase ∈ {"checking","starting","timeout","spawn_failed"}`
  - `overlay.js` 暴露 `window.glShellOverlay.show(message)` 与 `window.glShellOverlay.hide()`,幂等

- [ ] **Step 1: Write the failing tests**

`tests/desktop/test_assets.py`:

```python
# -*- coding: utf-8 -*-
"""引导页与浮层是壳靠 evaluate_js 驱动的,契约就是那几个全局函数名。

没有前端测试设施,所以这里只钉「壳依赖的名字确实存在、且资源自包含」——
弱,但能挡住重命名和外链。真正的行为验证在 Task 6 的浏览器实测与人工验收。
"""
from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "guanlan_v2" / "desktop"


def test_boot_page_exposes_the_hook_shell_calls():
    src = (_DIR / "boot.html").read_text(encoding="utf-8")
    assert "window.glBoot" in src and "setState" in src


def test_overlay_exposes_show_and_hide():
    src = (_DIR / "overlay.js").read_text(encoding="utf-8")
    assert "window.glShellOverlay" in src
    assert "show" in src and "hide" in src


def test_assets_are_self_contained():
    """壳的资源必须离线可用 —— 引导页正是在服务器不通时显示的。"""
    for name in ("boot.html", "overlay.js"):
        src = (_DIR / name).read_text(encoding="utf-8")
        assert "http://" not in src and "https://" not in src, f"{name} 外链了"


def test_both_assets_only_call_methods_that_JsApi_actually_has():
    """两处按钮硬编码了 API 名字;名字对不上就是一个点了没反应的按钮。"""
    from guanlan_v2.desktop.bridge import JsApi
    for name in ("boot.html", "overlay.js"):
        src = (_DIR / name).read_text(encoding="utf-8")
        for method in re.findall(r"api(?:\[')?\.?(\w+)(?:'\])?\(\)", src):
            if method in {"then", "catch"}:
                continue
            assert hasattr(JsApi, method), f"{name} 调了 JsApi 没有的 {method}()"
    # 两个按钮各自的方法必须真的被引用到
    boot = (_DIR / "boot.html").read_text(encoding="utf-8")
    overlay = (_DIR / "overlay.js").read_text(encoding="utf-8")
    assert "retry" in boot and "open_log" in boot
    assert "retry" in overlay and "open_log" in overlay
```

本文件顶部需要 `import re`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_assets.py -q`
Expected: FAIL —— `FileNotFoundError: ...boot.html`

- [ ] **Step 3: Write the implementation**

`guanlan_v2/desktop/boot.html` —— 配色抄自 `ui/_shared/tokens.css`(不外链,壳离线时也要能显示):

```html
<!doctype html>
<meta charset="utf-8">
<title>观澜</title>
<style>
  :root{--paper:#f1ead9;--paper-2:#ebe2cc;--ink:#1c1814;--ink-2:#6e6557;--ink-3:#9e9482;
        --line:#cfc4ab;--yin:#a8392d;--jin:#8a6f3f;
        --serif:"Noto Serif SC","Source Han Serif SC","Songti SC",serif;
        --mono:"JetBrains Mono","Consolas",monospace;}
  html,body{height:100%;margin:0;}
  body{background:linear-gradient(180deg,var(--paper),var(--paper-2));color:var(--ink);
       font-family:var(--serif);display:flex;align-items:center;justify-content:center;}
  .box{text-align:center;max-width:560px;padding:0 32px;}
  .seal{width:52px;height:52px;margin:0 auto 22px;background:var(--yin);color:var(--paper);
        font-size:30px;line-height:52px;border-radius:3px;}
  h1{font-size:19px;font-weight:600;letter-spacing:.22em;margin:0 0 26px;}
  #msg{font-size:14px;color:var(--ink-2);margin:0 0 10px;}
  #detail{font-family:var(--mono);font-size:11px;color:var(--ink-3);
          white-space:pre-wrap;word-break:break-all;margin:0 0 18px;}
  #warn{font-family:var(--mono);font-size:11px;color:var(--jin);margin:0 0 18px;display:none;}
  #acts{display:none;gap:10px;justify-content:center;}
  button{font-family:var(--serif);font-size:13px;padding:7px 20px;cursor:pointer;
         background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:2px;}
  button:hover{border-color:var(--yin);color:var(--yin);}
  .bar{height:2px;width:190px;margin:0 auto 20px;background:var(--line);overflow:hidden;}
  .bar i{display:block;height:100%;width:38%;background:var(--yin);
         animation:slide 1.25s ease-in-out infinite;}
  @keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(360%)}}
  .done .bar{visibility:hidden;}
</style>
<div class="box" id="box">
  <div class="seal">觀</div>
  <h1>觀 瀾</h1>
  <div class="bar"><i></i></div>
  <p id="msg">正在启动…</p>
  <p id="detail"></p>
  <p id="warn"></p>
  <div id="acts">
    <button id="retry">重试</button>
    <button id="log">看日志</button>
  </div>
</div>
<script>
  (function () {
    var box = document.getElementById('box');
    var acts = document.getElementById('acts');
    function api() { return (window.pywebview && window.pywebview.api) || null; }

    document.getElementById('retry').addEventListener('click', function () {
      var a = api(); if (a && a.retry) a.retry();
    });
    document.getElementById('log').addEventListener('click', function () {
      var a = api(); if (a && a.open_log) a.open_log();
    });

    window.glBoot = {
      setState: function (s) {
        document.getElementById('msg').textContent = s.message || '';
        document.getElementById('detail').textContent = s.detail || '';
        var w = document.getElementById('warn');
        w.textContent = s.warning || '';
        w.style.display = s.warning ? 'block' : 'none';
        var stuck = (s.phase === 'timeout' || s.phase === 'spawn_failed');
        acts.style.display = stuck ? 'flex' : 'none';
        box.className = stuck ? 'box done' : 'box';
      }
    };
  })();
</script>
```

`guanlan_v2/desktop/overlay.js` —— 壳在每次页面 loaded 后注入,再按需 show/hide:

```javascript
// 掉线浮层。壳把本文件的内容 evaluate 进任意页面,再调 show/hide。
// 用浮层而不换页:DOM 保住了,恢复后撤掉浮层即可,不必重载丢失页面状态。
(function () {
  if (window.glShellOverlay) return;            // 幂等:每次 loaded 都会注入一遍
  var ID = 'gl-shell-overlay';

  function build(message) {
    var d = document.createElement('div');
    d.id = ID;
    d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:flex;' +
      'align-items:center;justify-content:center;background:rgba(28,24,20,.72);' +
      'font-family:"Noto Serif SC",serif;color:#f1ead9;text-align:center;';
    var inner = document.createElement('div');
    inner.style.cssText = 'max-width:520px;padding:26px 34px;background:#1c1814;' +
      'border:1px solid #a8392d;border-radius:3px;';
    var h = document.createElement('div');
    h.style.cssText = 'font-size:15px;letter-spacing:.16em;margin-bottom:10px;';
    h.textContent = '与 9999 的连接中断';
    var p = document.createElement('div');
    p.className = 'gl-ov-msg';
    p.style.cssText = 'font-family:Consolas,monospace;font-size:11px;color:#9e9482;';
    p.textContent = message || '';
    inner.appendChild(h); inner.appendChild(p); d.appendChild(inner);

    var acts = document.createElement('div');
    acts.style.cssText = 'margin-top:16px;display:flex;gap:10px;justify-content:center;';
    acts.appendChild(button('重试', 'retry'));
    acts.appendChild(button('看日志', 'open_log'));
    inner.appendChild(acts);
    return d;
  }

  function button(label, method) {
    var b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'font-family:"Noto Serif SC",serif;font-size:13px;padding:6px 18px;' +
      'cursor:pointer;background:transparent;color:#f1ead9;border:1px solid #a8392d;border-radius:2px;';
    b.addEventListener('click', function () {
      var api = window.pywebview && window.pywebview.api;
      if (api && typeof api[method] === 'function') api[method]();
    });
    return b;
  }

  window.glShellOverlay = {
    show: function (message) {
      var cur = document.getElementById(ID);
      if (cur) { cur.querySelector('.gl-ov-msg').textContent = message || ''; return; }
      if (document.body) document.body.appendChild(build(message));
    },
    hide: function () {
      var cur = document.getElementById(ID);
      if (cur && cur.parentNode) cur.parentNode.removeChild(cur);
    }
  };
})();
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_assets.py -q`
Expected: PASS,**4 passed**

- [ ] **Step 5: Commit**

```bash
git add guanlan_v2/desktop/boot.html guanlan_v2/desktop/overlay.js tests/desktop/test_assets.py
git commit -m "feat(desktop): boot page and disconnect overlay, both offline-safe"
```

---

### Task 5: shell + 入口 —— 窗口真的开出来

**Files:**
- Create: `guanlan_v2/desktop/shell.py`
- Create: `guanlan_v2/desktop/__main__.py`
- Test: `tests/desktop/test_shell_wiring.py`

本任务先装依赖:

```bash
"G:/financial-analyst/.venv/Scripts/python.exe" -m pip install pywebview
```

**Interfaces:**
- Consumes: `supervisor.{ensure_running, probe, spawn_watchdog, ConnectionMonitor, APP_URL, port_contamination}`;`bridge.JsApi`
- Produces:
  - `shell.BOOT_URI: str` —— `boot.html` 的 `file://` URI
  - `shell.WINDOW_TITLE: str = "观澜"`
  - `shell.create_shell(*, webview_module) -> Shell`
  - `class Shell` —— `.start()` 阻塞跑 GUI;`.open_ui_window(url)` 建新窗;`.status()` 返回 dict
  - `__main__.main() -> int`

- [ ] **Step 1: Write the failing tests**

`tests/desktop/test_shell_wiring.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_shell_wiring.py -q`
Expected: FAIL —— `ImportError: cannot import name 'shell'`

- [ ] **Step 3: Write the implementation**

`guanlan_v2/desktop/shell.py`:

```python
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
```

`guanlan_v2/desktop/__main__.py`:

```python
# -*- coding: utf-8 -*-
"""入口:`pythonw -m guanlan_v2.desktop`。

仓根引导兜底:正常经 `-m` 启动时(快捷方式的「起始位置」= 仓根)仓根本就在
sys.path 上,但 2026-07-26 那次 9999 起不来的教训是——生产启动形态和测试
启动形态不一样时,没人会发现。这里无条件兜一手,代价是三行。
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> int:
    import webview  # noqa: PLC0415 —— 延迟到 GUI 真要跑时才导

    from guanlan_v2.desktop.shell import create_shell

    create_shell(webview_module=webview).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/ -q`
Expected: PASS,**78 passed**(67 + 4 + 7)

- [ ] **Step 5: 真机验证 —— 窗口必须真的开出来**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m guanlan_v2.desktop`(在仓根跑)

Expected:一个 1400×900 的窗口出现,先显示朱红「觀」印章的引导页,随后跳到帷幄页。**若窗口没出现或跳不过去,不要进 Step 6 —— 回来修。** 把观察到的现象记进任务报告。

- [ ] **Step 6: Commit**

```bash
git add guanlan_v2/desktop/shell.py guanlan_v2/desktop/__main__.py tests/desktop/test_shell_wiring.py
git commit -m "feat(desktop): pywebview shell - window first, server second"
```

---

### Task 6: 多窗口 —— 顶栏的桌面分支

**Files:**
- Modify: `ui/_shared/guanlan-nav.js`(在 `MODULES.forEach` 之前插入辅助函数,在其内部与 brand/right 锚点上绑定)
- Test: `tests/desktop/test_nav_desktop_branch.py`

**Interfaces:**
- Consumes: Task 2 的 `JsApi.open_window`(网页侧即 `window.pywebview.api.open_window`);Task 5 注入的 `window.GL_DESKTOP`
- Produces: 无 Python 接口。行为契约见下。

- [ ] **Step 1: Write the failing test**

`tests/desktop/test_nav_desktop_branch.py`:

```python
# -*- coding: utf-8 -*-
"""顶栏被八个页面共用,浏览器也吃同一份 —— 所以桌面分支必须是条件化的。

仓库没有前端测试设施,本文件只做源码级守卫:挡住「无条件开新窗」这类会
弄坏浏览器行为的改法。真行为验证在 Step 4 用真浏览器做。
"""
from __future__ import annotations

import re
from pathlib import Path

_NAV = Path(__file__).resolve().parents[2] / "ui" / "_shared" / "guanlan-nav.js"


def test_embed_guard_is_still_the_very_first_thing():
    """?embed=1 提前 return 是帷幄右栏 iframe 的嵌入卫生,不能被挤到后面。"""
    src = _NAV.read_text(encoding="utf-8")
    embed_at = src.index("embed")
    assert embed_at < src.index("MODULES"), "embed 卫生必须在建导航之前"


def test_new_window_is_gated_on_a_desktop_signal():
    src = _NAV.read_text(encoding="utf-8")
    assert "open_window" in src, "桌面分支不在了"
    # open_window 只能出现在同时提到 GL_DESKTOP 或 pywebview 的守卫之后
    assert "GL_DESKTOP" in src and "pywebview" in src


def test_both_click_and_auxclick_are_bound():
    """click 在现代浏览器里中键不触发;只挂 click 会让中键静默失效。"""
    src = _NAV.read_text(encoding="utf-8")
    assert "'auxclick'" in src or '"auxclick"' in src
    assert "'click'" in src or '"click"' in src


def test_plain_left_click_is_not_intercepted():
    """必须有修饰键/中键判断 —— 否则普通左键点也会被 preventDefault。"""
    src = _NAV.read_text(encoding="utf-8")
    assert "ctrlKey" in src and "button" in src


def test_missing_bridge_falls_back_instead_of_breaking_navigation():
    """桥不在时不能 preventDefault,否则页面点了没反应。"""
    src = _NAV.read_text(encoding="utf-8")
    guard = re.search(r"if \(!api\)\s*return;", src)
    assert guard, "缺少「桥不在就退回普通跳转」的早退"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_nav_desktop_branch.py -q`
Expected: FAIL —— `AssertionError: 桌面分支不在了`

- [ ] **Step 3: Modify guanlan-nav.js**

在 `var here = '';` 那一行**之前**插入:

```javascript
  // ── 桌面壳分支 ────────────────────────────────────────────────────
  // 顶栏被八个页面共用,浏览器里也吃这一份。所以这段必须条件化:浏览器里
  // GL_DESKTOP 与 pywebview 都不存在,glDesktopApi() 返回 null,一切照旧。
  // 两个信号取其一:GL_DESKTOP 由壳在 loaded 时注入(可移植,但对 parse 时
  // 执行的本脚本有竞态);window.pywebview 由 pywebview 自己注入(无竞态)。
  // 都只在点击那一刻读,所以竞态实际不成立,两者并存只为将来换壳。
  function glDesktopApi() {
    var api = window.pywebview && window.pywebview.api;
    if (!api || typeof api.open_window !== 'function') return null;
    return api;
  }
  function glWantsNewWindow(e) {
    return e.button === 1 || e.ctrlKey || e.metaKey;   // 中键 / Ctrl / Cmd
  }
  function glOnActivate(e) {
    if (!glWantsNewWindow(e)) return;                  // 普通左键 → 原地跳转
    var api = glDesktopApi();
    if (!api) return;                                  // 桥不在 → 不拦截,退回普通跳转
    e.preventDefault();
    try {
      api.open_window(new URL(e.currentTarget.getAttribute('href'), location.href).href);
    } catch (err) { /* 开窗失败不该弄坏顶栏 */ }
  }
  function glBindNewWindow(a) {
    a.addEventListener('click', glOnActivate);
    a.addEventListener('auxclick', glOnActivate);      // click 不为中键触发
  }
```

在 `MODULES.forEach` 内 `bar.appendChild(a);` **之前**加一行:

```javascript
    glBindNewWindow(a);
```

在 `bar.appendChild(right);` **之前**加一行:

```javascript
  glBindNewWindow(right);
```

brand 锚点是 `bar.innerHTML` 拼出来的,取回来再绑 —— 在 `MODULES.forEach(...)` **之前**加:

```javascript
  var brand = bar.querySelector('#gl-brand');
  if (brand) glBindNewWindow(brand);
```

- [ ] **Step 4: Run test + 真浏览器验证浏览器行为没变**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_nav_desktop_branch.py -q`
Expected: PASS,5 passed

然后用浏览器工具实测(9999 已在跑):打开 `http://127.0.0.1:9999/ui/screen/观澜 · 选股.html`,**Ctrl+点**顶栏「选股」以外的任一模块。
Expected:仍然是**原地跳转**(浏览器里没有桥 → `glDesktopApi()` 返回 null → 不 preventDefault)。若变成弹窗或点了没反应,回来修。

- [ ] **Step 5: 真机验证桌面行为**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m guanlan_v2.desktop`,在窗口里 **Ctrl+点**顶栏另一个模块。
Expected:开出**第二个窗口**,加载那个模块;原窗口不动。

- [ ] **Step 6: Commit**

```bash
git add ui/_shared/guanlan-nav.js tests/desktop/test_nav_desktop_branch.py
git commit -m "feat(desktop): Ctrl/middle-click the nav bar opens a second window"
```

---

### Task 7: 图标与快捷方式 —— 「点击能启动」

**Files:**
- Create: `scripts/make_desktop_icon.py`
- Create: `guanlan_v2/desktop/guanlan.ico`(由上面的脚本生成后提交)
- Create: `scripts/install_desktop_shortcut.ps1`
- Test: `tests/desktop/test_launch_form.py`

**Interfaces:**
- Consumes: Task 5 的 `__main__`
- Produces: 桌面与开始菜单上的 `观澜.lnk`

- [ ] **Step 1: Write the failing test**

`tests/desktop/test_launch_form.py`:

```python
# -*- coding: utf-8 -*-
"""快捷方式的启动形态守卫。

2026-07-26 晚 9999 起不来,根因是生产以**脚本**形式跑 server.py,sys.path[0]
成了包目录、仓根不在路径上,而全套件绿(测试与 `-m` 都自带仓根)。桌面壳是
一个新的生产启动点,这里把它钉在 `-m` 形态上。同类守卫见
tests/test_server_script_launch.py。
"""
from __future__ import annotations

from pathlib import Path

_PS1 = Path(__file__).resolve().parents[2] / "scripts" / "install_desktop_shortcut.ps1"
_ICON = Path(__file__).resolve().parents[2] / "guanlan_v2" / "desktop" / "guanlan.ico"


def test_shortcut_launches_via_dash_m_not_a_script_path():
    src = _PS1.read_text(encoding="utf-8-sig")
    assert "-m guanlan_v2.desktop" in src
    assert "desktop\\__main__.py" not in src and "desktop/__main__.py" not in src


def test_shortcut_uses_pythonw_so_no_console_window_flashes():
    assert "pythonw.exe" in _PS1.read_text(encoding="utf-8-sig")


def test_shortcut_working_directory_is_the_repo_root():
    src = _PS1.read_text(encoding="utf-8-sig")
    assert "WorkingDirectory" in src and "guanlan-v2" in src


def test_ps1_comments_are_ascii_only():
    """PS 5.1 把无 BOM UTF-8 的非 ASCII 注释当 ANSI 读 → 语法炸(memory 大坑③)。"""
    for i, line in enumerate(_PS1.read_text(encoding="utf-8-sig").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            assert stripped.isascii(), f"line {i}: non-ASCII comment"


def test_icon_exists_and_is_an_ico():
    assert _ICON.exists(), "图标未生成"
    assert _ICON.read_bytes()[:4] == b"\x00\x00\x01\x00", "不是 ICO 文件头"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/test_launch_form.py -q`
Expected: FAIL —— `FileNotFoundError: ...install_desktop_shortcut.ps1`

- [ ] **Step 3a: 写图标生成器并跑一次**

`scripts/make_desktop_icon.py`:

```python
# -*- coding: utf-8 -*-
"""一次性生成桌面壳图标:朱红印章上一个「觀」字。

**构建期脚本**。pillow 只在跑本脚本时需要,不进运行时依赖 —— 产物 .ico
已提交进仓库,除非要改图标,否则不必再跑。

    pip install pillow
    python scripts/make_desktop_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_OUT = Path(__file__).resolve().parents[1] / "guanlan_v2" / "desktop" / "guanlan.ico"
_YIN = (168, 57, 45)        # --yin 印章红
_PAPER = (241, 234, 217)    # --paper 宣纸暖白
_FONT = "C:/Windows/Fonts/msyhbd.ttc"   # 微软雅黑 Bold,Windows 自带
_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    radius = max(1, size // 12)
    d.rounded_rectangle([pad, pad, size - pad - 1, size - pad - 1], radius=radius, fill=_YIN)
    font = ImageFont.truetype(_FONT, int(size * 0.62))
    box = d.textbbox((0, 0), "觀", font=font)
    d.text(((size - (box[2] - box[0])) / 2 - box[0],
            (size - (box[3] - box[1])) / 2 - box[1]), "觀", font=font, fill=_PAPER)
    return img


def main() -> None:
    frames = [_render(s) for s in _SIZES]
    frames[-1].save(_OUT, format="ICO", sizes=[(s, s) for s in _SIZES])
    print(f"wrote {_OUT} ({_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
```

Run:
```bash
"G:/financial-analyst/.venv/Scripts/python.exe" -m pip install pillow
"G:/financial-analyst/.venv/Scripts/python.exe" scripts/make_desktop_icon.py
```
Expected: `wrote G:\guanlan-v2\guanlan_v2\desktop\guanlan.ico (NNNNN bytes)`

- [ ] **Step 3b: 写快捷方式安装脚本**

`scripts/install_desktop_shortcut.ps1` —— **注释全 ASCII**;因含中文字符串字面量,**必须存为带 BOM 的 UTF-8**:

```powershell
# install_desktop_shortcut.ps1 -- create the Guanlan desktop shell shortcut.
#
# Idempotent: re-running overwrites the two .lnk files in place.
# ASCII-only comments -- PS 5.1 misparses BOM-less UTF-8 non-ASCII bytes.
# This file MUST be saved as UTF-8 WITH BOM because it contains CJK string
# literals (the shortcut display name). Without the BOM PS 5.1 reads them
# as ANSI and the shortcut name comes out as mojibake.
#
# Launch form is `-m guanlan_v2.desktop`, NOT a script path: running a file
# inside the package puts the PACKAGE dir on sys.path[0] and leaves the repo
# root off it. That is exactly how 9999 was brought down on 2026-07-26.
$ErrorActionPreference = 'Stop'

$Repo    = 'G:\guanlan-v2'
$Pythonw = 'G:\financial-analyst\.venv\Scripts\pythonw.exe'
$Icon    = Join-Path $Repo 'guanlan_v2\desktop\guanlan.ico'
$Name    = '观澜.lnk'

foreach ($p in @($Pythonw, $Icon)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) $Name),
    (Join-Path ([Environment]::GetFolderPath('Programs')) $Name)
)

$ws = New-Object -ComObject WScript.Shell
foreach ($t in $targets) {
    $dir = Split-Path $t -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $lnk = $ws.CreateShortcut($t)
    $lnk.TargetPath       = $Pythonw
    $lnk.Arguments        = '-m guanlan_v2.desktop'
    $lnk.WorkingDirectory = $Repo
    $lnk.IconLocation     = $Icon
    $lnk.Description      = 'Guanlan desktop shell'
    $lnk.WindowStyle      = 1
    $lnk.Save()
    Write-Output "wrote $t"
}
```

写完后加 BOM:

```bash
powershell -NoProfile -Command "$p='G:\guanlan-v2\scripts\install_desktop_shortcut.ps1'; $c=Get-Content -Raw -Encoding UTF8 $p; Set-Content -Path $p -Value $c -Encoding UTF8"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/desktop/ -q`
Expected: PASS,**88 passed**(78 + 5 + 5)

- [ ] **Step 5: 真机验证「点击能启动」**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_desktop_shortcut.ps1`
Expected:两行 `wrote ...`,且桌面上出现名为 **观澜**(不是乱码)、图标为朱红印章的快捷方式。

然后**双击它**。Expected:引导页 → 帷幄页,任务栏里是独立图标,没有控制台窗口一闪。

- [ ] **Step 6: Commit**

```bash
git add scripts/make_desktop_icon.py scripts/install_desktop_shortcut.ps1 guanlan_v2/desktop/guanlan.ico tests/desktop/test_launch_form.py
git commit -m "feat(desktop): icon plus idempotent shortcut installer pinned to the -m launch form"
```

---

### Task 8: 全套件回归与收尾

**Files:**
- Modify: `docs/README.md` **不要动** —— 该文件被并发 session 持有。改 `guanlan_v2/desktop/__init__.py` 的模块 docstring 记录用法即可。

- [ ] **Step 1: 跑全套件**

Run: `"G:/financial-analyst/.venv/Scripts/python.exe" -m pytest tests/ -q`
Expected: 全绿。基线是本计划开工前的数字加上本计划新增的 **88** 条;若有**任何**先前通过的测试变红,那是本计划弄坏的,回去修,不要标注为「预先存在」。

- [ ] **Step 2: 确认没有碰到别人的文件**

Run: `git status --porcelain`
Expected:并发 session 的那批脏文件(`guanlan_v2/console/`、`guanlan_v2/datafeed/`、`guanlan_v2/glmcp/`、`ui/screen/`、`docs/README.md`、`.data/`)状态与开工前**一模一样**;工作区里除它们之外应当是干净的。

- [ ] **Step 3: 确认 9999 仍然健在**

Run: `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:9999/workflow/list`
Expected: `200`。壳的任何一步都不该动到 9999。

---

## Self-Review

**Spec coverage:**

| Spec 条目 | 落在哪 |
| --- | --- |
| §3 模块边界(5 文件) | Task 1/2/4/5 |
| §3.1 只启动不杀 | Task 1 测试 `test_ensure_healthy_on_first_probe_never_spawns` + supervisor 无任何 kill 路径 |
| §4 启动序列 / 90s / 帷幄落地 | Task 1 `ensure_running` + Task 5 `run_boot_sequence` |
| §4.1 规矩 1 剔除 GUANLAN_PORT | Task 1 三条测试 |
| §4.1 规矩 2 `-m` 形态 | Task 5 `__main__` + Task 7 `test_shortcut_launches_via_dash_m_not_a_script_path` |
| §5 多窗口 / auxclick / 桥不在降级 / 浏览器不变 | Task 6 |
| §5.1 安全闸 | Task 2 |
| §6 心跳进程级 / 一次掉线拉一次 | Task 3 + Task 5 `heartbeat_once` |
| §7 交付物 | Task 7 |
| §8 测试与验证 | 各任务 Step 4/5 |

**已知偏离 spec 之处(实现更强,已在设计评审外向用户口头说明):**
1. 派生用 `subprocess.Popen(env=...)`(与 `server.py:_checker_revive_loop` 同款)而非 WMI —— WMI `Win32_Process.Create` 不让控制环境,那样「剔除 GUANLAN_PORT」就无法字面实现也无法测试。
2. 桌面检测同时认 `GL_DESKTOP` 与 `window.pywebview`,后者无竞态。

**Type consistency:** `EnsureOutcome.state` 的三个取值在 Task 1、Task 5 测试与 `run_boot_sequence` 分支里一致;`UrlVerdict.reason` 的 slug 在 Task 2 测试与 `validate_ui_url` 里一致;`MonitorDecision` 四个布尔字段在 Task 3 与 Task 5 `heartbeat_once` 里一致;`JsApi` **四个**构造参数名(`open_window_factory` / `status_provider` / `retry_handler` / `log_opener`)在 Task 2、Task 5 里一致。

**自查抓到并已修的两处**(记在这里,因为它们正是这类计划最典型的失败形状):

1. `boot.html` 的 [看日志] 按钮调 `api.open_log()`,而 Task 2 原本的 `JsApi` 只有三个方法 —— **一个点了没反应的按钮**,而且没有任何测试会发现。修法不只是补上方法:Task 2 增加了 `test_every_page_facing_method_exists`,Task 4 增加了 `test_both_assets_only_call_methods_that_JsApi_actually_has`(从 HTML/JS 源码里正则抽出 `api.xxx()` 调用,逐个 `hasattr` 比对),把「网页硬编码的名字」和「Python 真有的方法」这条跨语言契约钉死。
2. 设计 §6 说掉线浮层带 [重试] [看日志] 两个按钮,而 Task 4 原本的 `overlay.js` 只有文字。已补上按钮。
