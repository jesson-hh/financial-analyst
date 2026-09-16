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
_PKG_DIR = _SERVER_PY.parent
_SENTINEL = "\napp = create_app()"


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


def test_production_bootstrap_prefers_vendored_engine_and_keeps_llm_abi(
        tmp_path: Path) -> None:
    """生产启动必须压过 venv 里的同名 editable install,并提供编排所需 ABI。

    临时同名包放在子进程的初始 ``sys.path[0]``,比真实 editable
    install 更强地模拟“外部旧包本应抢先命中”。随后按生产脚本形态执行
    server 前导代码,由 ``_ensure_engine_importable`` 翻转优先级。LLM 调用
    用本地 async stub 截断,不导入 provider SDK,也不发网络请求。
    """
    external = tmp_path / "external-editable"
    (external / "financial_analyst" / "llm").mkdir(parents=True)
    (external / "financial_analyst" / "__init__.py").write_text(
        "ORIGIN = 'external-editable'\n", encoding="utf-8")
    (external / "financial_analyst" / "llm" / "__init__.py").write_text(
        "", encoding="utf-8")
    (external / "financial_analyst" / "llm" / "client.py").write_text(
        "class LLMClient:\n    pass\n", encoding="utf-8")

    engine = _REPO / "engine"
    driver = f"""
import asyncio
import importlib.util
import inspect
import os
import sys
from pathlib import Path

repo = Path({str(_REPO)!r}).resolve()
engine = Path({str(engine)!r}).resolve()
external = Path({str(external)!r}).resolve()

def norm(value):
    try:
        return Path(value).resolve()
    except (OSError, TypeError, ValueError):
        return None

# 模拟 venv editable install 已经在搜索路径最前;排除 pytest 继承的仓内 engine。
sys.path[:] = [str(external), str(repo)] + [
    p for p in sys.path
    if norm(p) not in {{repo, engine, external}}
]
os.environ.pop("GUANLAN_FA_SRC", None)
for name in tuple(sys.modules):
    if name == "financial_analyst" or name.startswith("financial_analyst."):
        del sys.modules[name]

before = importlib.util.find_spec("financial_analyst")
assert before is not None and Path(before.origin).resolve().is_relative_to(external), before

source = Path({str(_SERVER_PY)!r}).read_text(encoding="utf-8")
namespace = {{"__name__": "guanlan_v2.server", "__file__": {str(_SERVER_PY)!r}}}
exec(compile(source.split({_SENTINEL!r})[0], {str(_SERVER_PY)!r}, "exec"), namespace)
resolved = Path(namespace["_ensure_engine_importable"]()).resolve()
assert resolved.is_relative_to(engine), resolved

import financial_analyst.llm.client as client_module
LLMClient = client_module.LLMClient
assert Path(client_module.__file__).resolve().is_relative_to(engine), client_module.__file__

client = LLMClient(
    provider="deepseek",
    model="deepseek-v4-flash",
    config={{"max_retries": 0}},
    max_tokens=4096,
    timeout=90,
    thinking=True,
    reasoning_effort="high",
)
assert client.default_max_tokens == 4096
assert client.default_timeout == 90.0
assert client.default_thinking is True
assert client.default_reasoning_effort == "high"

required = {{
    "max_tokens", "timeout", "thinking", "reasoning_effort",
    "max_retries", "on_dispatch",
}}
assert required <= set(inspect.signature(LLMClient.chat).parameters)
captured = {{}}

async def no_network(messages, tools, response_format, temperature, *,
                     max_tokens, timeout, thinking, reasoning_effort,
                     transport_max_retries, on_dispatch):
    captured.update(
        max_tokens=max_tokens,
        timeout=timeout,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        transport_max_retries=transport_max_retries,
        on_dispatch=on_dispatch,
    )
    return "ABI-OK"

client._chat_openai_compat = no_network
result = asyncio.run(client.chat(
    [{{"role": "user", "content": "offline ABI probe"}}],
    max_tokens=1024,
    timeout=12.5,
    thinking=True,
    reasoning_effort="max",
    max_retries=0,
))
assert result == "ABI-OK"
assert captured == {{
    "max_tokens": 1024,
    "timeout": 12.5,
    "thinking": True,
    "reasoning_effort": "max",
    "transport_max_retries": 0,
    "on_dispatch": None,
}}
print("ENGINE-ABI-OK")
"""

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=110,
    )

    assert proc.returncode == 0 and "ENGINE-ABI-OK" in proc.stdout, (
        "生产启动未能稳定选中仓内 engine,或 LLMClient ABI 与编排层失配。\n"
        f"--- returncode {proc.returncode} ---\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n"
        f"--- stderr ---\n{proc.stderr[-3000:]}"
    )
