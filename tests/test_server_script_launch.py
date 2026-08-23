# -*- coding: utf-8 -*-
"""生产是把 server.py 当**脚本**跑的,这条路径此前没有任何测试。

`scripts/check_9999.ps1` 的 `Start-Server` 执行

    G:\\financial-analyst\\.venv\\Scripts\\python.exe G:\\guanlan-v2\\guanlan_v2\\server.py

CPython 对脚本形式把 ``sys.path[0]`` 设成**脚本所在目录**(即 ``guanlan_v2/`` 这个包目录),
仓根**不在** sys.path 上。于是 ``import guanlan_v2`` 在生产里会 ModuleNotFoundError,
而在下面这些地方全都不会:

* 文档里写的 ``python -m guanlan_v2.server``(``-m`` 把 cwd 放进 sys.path);
* pytest(conftest 把仓根 prepend);
* 任何 ``import guanlan_v2.server`` 的测试。

2026-07-26 就是这么炸的:R23/R24 在模块层加了一句 ``from guanlan_v2 import
orch_store_status``,全套件绿,而 9999 起不来——看门狗每 30 秒重启一次,每次都死在那一行。
server.py 里其余所有 ``guanlan_v2.*`` 导入都躲在 ``create_app()`` 函数体内,所以只有
模块层这一句碰到了这条没人测的路径。

这条测试把生产的 sys.path 形状照搬过来,执行 server.py 的**整段模块层代码**(到
``app = create_app()`` 为止,那一句太重不在单测里跑),因此将来任何人在模块层新加导入
都会被它挡住,不只是当年那一句。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SERVER_PY = _REPO / "guanlan_v2" / "server.py"
_ENV_EXAMPLE = _REPO / ".env.example"
_PKG_DIR = _SERVER_PY.parent
_SENTINEL = "\napp = create_app()"


def _run_prologue(
    body: str, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute the lightweight server definitions, then one focused driver.

    This keeps the production script-shaped ``sys.path`` while avoiding the
    heavyweight module-level ``app = create_app()`` side effect.
    """
    driver = f"""
import sys
_repo = {str(_REPO)!r}
_pkg = {str(_PKG_DIR)!r}
sys.path[:] = [_pkg] + [p for p in sys.path[1:] if Path_norm(p) != Path_norm(_repo)]
_src = open({str(_SERVER_PY)!r}, encoding="utf-8").read()
_prologue = _src.split({_SENTINEL!r})[0]
_ns = {{"__name__": "guanlan_v2.server", "__file__": {str(_SERVER_PY)!r}}}
exec(compile(_prologue, {str(_SERVER_PY)!r}, "exec"), _ns)
exec(compile({body!r}, "<server-prologue-driver>", "exec"), _ns)
"""
    preamble = (
        "import os\n"
        "def Path_norm(p):\n"
        "    return os.path.normcase(os.path.normpath(p))\n"
    )
    return subprocess.run(
        [sys.executable, "-c", preamble + driver],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=110,
    )


def _assert_driver_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, (
        f"--- returncode {proc.returncode} ---\n"
        f"STDOUT:\n{proc.stdout[-3000:]}\nSTDERR:\n{proc.stderr[-3000:]}"
    )


def test_sentinel_still_marks_the_end_of_the_prologue() -> None:
    """切片哨兵必须真的在 server.py 里,否则下面那条测试会静默退化成"整个文件"或"空"。"""
    src = _SERVER_PY.read_text(encoding="utf-8")
    assert src.count(_SENTINEL) == 1, (
        f"expected exactly one {_SENTINEL!r} in server.py; the prologue slice below "
        "depends on it. If create_app() is invoked differently now, update _SENTINEL."
    )
    prologue = src.split(_SENTINEL)[0]
    assert "def create_app" in prologue, "slice landed before create_app's definition"


def test_module_level_code_runs_with_only_the_package_dir_on_syspath() -> None:
    """照搬生产的 sys.path 形状跑 server.py 的模块层。仓根不在路径上。

    仓根被显式**剔除**(而不是只靠不添加):pytest 自己的 conftest 已经把它 prepend 了,
    子进程会通过 PYTHONPATH 之类的渠道继承,不剔除的话这条测试永远绿,正是它要防的那种绿。
    """
    driver = f"""
import sys
_repo = {str(_REPO)!r}
_pkg = {str(_PKG_DIR)!r}
sys.path[:] = [_pkg] + [p for p in sys.path[1:] if Path_norm(p) != Path_norm(_repo)]
_src = open({str(_SERVER_PY)!r}, encoding="utf-8").read()
_prologue = _src.split({_SENTINEL!r})[0]
exec(compile(_prologue, {str(_SERVER_PY)!r}, "exec"),
     {{"__name__": "guanlan_v2.server", "__file__": {str(_SERVER_PY)!r}}})
print("PROLOGUE-OK")
"""
    # Path_norm 先定义再用 —— 大小写与分隔符都归一,Windows 上 'G:\\x' 与 'g:/x' 是同一个目录。
    preamble = (
        "import os\n"
        "def Path_norm(p):\n"
        "    return os.path.normcase(os.path.normpath(p))\n"
    )

    proc = subprocess.run(
        [sys.executable, "-c", preamble + driver],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=110,
    )

    assert proc.returncode == 0 and "PROLOGUE-OK" in proc.stdout, (
        "server.py 的模块层代码在生产的 sys.path 形状下跑不起来 —— 9999 会起不来。\n"
        "多半是有人在模块层加了 `import guanlan_v2.<...>`;挪进 create_app() 函数体,\n"
        "或者确认文件顶部那段仓根引导仍在该导入之前。\n"
        f"--- returncode {proc.returncode} ---\n{proc.stderr[-3000:]}"
    )


def test_telegram_gate_is_exact_opt_in_and_import_time_remains_side_effect_free() -> None:
    env = dict(os.environ)
    env["GUANLAN_TELEGRAM_ENABLED"] = "1"
    proc = _run_prologue(
        """
import os
import sys
assert _telegram_feature_enabled() is True
assert _telegram_feature_enabled({"GUANLAN_TELEGRAM_ENABLED": "0"}) is False
assert _telegram_feature_enabled({"GUANLAN_TELEGRAM_ENABLED": "true"}) is False
assert _telegram_feature_enabled({"GUANLAN_TELEGRAM_ENABLED": " 1 "}) is False
assert _telegram_feature_enabled({}) is False
assert not any(name.startswith("guanlan_v2.channels") for name in sys.modules)
print("TELEGRAM-GATE-OK")
""",
        env=env,
    )
    _assert_driver_ok(proc)
    assert "TELEGRAM-GATE-OK" in proc.stdout


def test_env_example_documents_disabled_gate_without_a_token_slot() -> None:
    sample = _ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "GUANLAN_TELEGRAM_ENABLED=0" in sample
    assert "TELEGRAM_BOT_TOKEN" not in sample


def test_channel_configuration_does_not_touch_components_when_disabled() -> None:
    proc = _run_prologue(
        """
class State:
    pass

class App:
    def __init__(self):
        self.state = State()
        self.routers = []
    def include_router(self, router):
        self.routers.append(router)

def forbidden(*args, **kwargs):
    raise AssertionError("disabled gate constructed a Channel component")

app = App()
console_kwargs = {"existing": "kept"}
result = _configure_telegram_channel(
    app,
    object(),
    console_kwargs,
    enabled=False,
    runtime_factory=forbidden,
    router_factory=forbidden,
)
assert result is None
assert app.state.channel_runtime is None
assert console_kwargs == {"existing": "kept", "channel_runtime": None}
assert app.routers == []
print("TELEGRAM-OFF-OK")
"""
    )
    _assert_driver_ok(proc)
    assert "TELEGRAM-OFF-OK" in proc.stdout


def test_channel_configuration_shares_one_runtime_with_router_console_and_state() -> None:
    proc = _run_prologue(
        """
class State:
    pass

class App:
    def __init__(self):
        self.state = State()
        self.routers = []
    def include_router(self, router):
        self.routers.append(router)

console_store = object()
runtime = object()
calls = []
def build_runtime(store):
    calls.append(("runtime", store))
    return runtime
def build_router(supplied):
    calls.append(("router", supplied))
    return ("channel-router", supplied)

app = App()
console_kwargs = {}
result = _configure_telegram_channel(
    app,
    console_store,
    console_kwargs,
    enabled=True,
    runtime_factory=build_runtime,
    router_factory=build_router,
)
assert result is runtime
assert calls == [("runtime", console_store), ("router", runtime)]
assert console_kwargs["channel_runtime"] is runtime
assert app.state.channel_runtime is runtime
assert app.routers == [("channel-router", runtime)]
print("TELEGRAM-ON-OK")
"""
    )
    _assert_driver_ok(proc)
    assert "TELEGRAM-ON-OK" in proc.stdout


def test_production_channel_factory_uses_console_root_and_explicit_calendar(tmp_path) -> None:
    proc = _run_prologue(
        f"""
from pathlib import Path

calls = []
class Console:
    root = Path({str(tmp_path)!r})

class Value:
    def __init__(self, kind, value=None):
        self.kind = kind
        self.value = value

def channel_store(path):
    calls.append(("channel_store", Path(path)))
    return Value("channel_store", Path(path))
def secret_store(path):
    calls.append(("secret_store", Path(path)))
    return Value("secret_store", Path(path))
def media_store(console):
    calls.append(("media_store", console))
    return Value("media_store", console)
def telegram_adapter():
    calls.append(("telegram_adapter",))
    return Value("telegram_adapter")
def calendar():
    calls.append(("calendar",))
    return Value("calendar")
def runtime(**kwargs):
    calls.append(("runtime", kwargs))
    return Value("runtime", kwargs)

console = Console()
made = _build_telegram_channel_runtime(
    console,
    factories={{
        "channel_store": channel_store,
        "secret_store": secret_store,
        "media_store": media_store,
        "telegram_adapter": telegram_adapter,
        "trading_calendar": calendar,
        "runtime": runtime,
    }},
)
assert made.kind == "runtime"
assert calls[0] == ("channel_store", console.root / "channels.sqlite3")
assert calls[1] == ("secret_store", console.root / "channel_secrets")
assert calls[2] == ("media_store", console)
assert calls[3] == ("telegram_adapter",)
assert calls[4] == ("calendar",)
kwargs = calls[5][1]
assert kwargs["console_store"] is console
assert kwargs["channel_store"].value == console.root / "channels.sqlite3"
assert kwargs["secret_store"].value == console.root / "channel_secrets"
assert kwargs["media_store"].value is console
assert kwargs["telegram_adapter"].kind == "telegram_adapter"
assert kwargs["automation_calendar"].kind == "calendar"
print("TELEGRAM-FACTORY-OK")
"""
    )
    _assert_driver_ok(proc)
    assert "TELEGRAM-FACTORY-OK" in proc.stdout


def test_channel_lifespan_starts_and_stops_once_including_exception_path() -> None:
    proc = _run_prologue(
        """
import asyncio

class Runtime:
    def __init__(self):
        self.started = 0
        self.stopped = 0
    async def start(self):
        self.started += 1
    async def stop(self):
        self.stopped += 1

class StartFailRuntime(Runtime):
    async def start(self):
        self.started += 1
        raise RuntimeError("startup failed")

async def exercise(raise_inside):
    runtime = Runtime()
    try:
        async with _telegram_runtime_lifespan(runtime):
            assert runtime.started == 1
            assert runtime.stopped == 0
            if raise_inside:
                raise RuntimeError("lifespan body failed")
    except RuntimeError:
        assert raise_inside
    assert runtime.started == 1
    assert runtime.stopped == 1

async def main():
    await exercise(False)
    await exercise(True)
    failed = StartFailRuntime()
    try:
        async with _telegram_runtime_lifespan(failed):
            raise AssertionError("failed startup entered lifespan body")
    except RuntimeError:
        pass
    assert failed.started == 1
    assert failed.stopped == 1
    async with _telegram_runtime_lifespan(None):
        pass

asyncio.run(main())
print("TELEGRAM-LIFESPAN-OK")
"""
    )
    _assert_driver_ok(proc)
    assert "TELEGRAM-LIFESPAN-OK" in proc.stdout
