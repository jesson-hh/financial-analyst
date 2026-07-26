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
