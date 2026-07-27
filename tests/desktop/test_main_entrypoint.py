# -*- coding: utf-8 -*-
"""guanlan_v2/desktop/__main__.py 的两条守护:

1. 仓根引导守护(同构于 tests/test_server_script_launch.py 对 server.py 的守护)。
   正常经 `python -m guanlan_v2.desktop` 启动时,__main__.py 顶部那三行 sys.path
   引导看起来什么都没做 —— cwd(生产快捷方式的"起始位置" = 仓根)本来就已经在
   sys.path 上了。这条"什么都没做"的观感正是它容易被当成死代码删掉的原因。
   这里照搬"把 __main__.py 当裸脚本执行"的最坏形状(sys.path[0] = 包自己的目录,
   仓根不在路径上的任何地方),证明没有这三行,后续
   `from guanlan_v2.desktop.shell import create_shell` 会 ModuleNotFoundError;
   有这三行,能成功导入。

2. 崩溃留痕:`import webview` 失败或 WebView2 运行时缺失时,`pythonw -m
   guanlan_v2.desktop` 不该是一次双击后什么都不出的静默失败 —— 那正是引导页
   本身要防的失败模式。main() 必须把原因写进一个磁盘上的文件。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MAIN_PY = _REPO / "guanlan_v2" / "desktop" / "__main__.py"
_SENTINEL = '\nif __name__ == "__main__":'


def test_sentinel_still_marks_the_end_of_the_prologue() -> None:
    """切片哨兵必须真的在 __main__.py 里,否则下面那条测试会静默退化。"""
    src = _MAIN_PY.read_text(encoding="utf-8")
    assert src.count(_SENTINEL) == 1, (
        f"expected exactly one {_SENTINEL!r} in __main__.py; the prologue slice below depends on it."
    )
    prologue = src.split(_SENTINEL)[0]
    assert "_REPO_ROOT" in prologue and "def main" in prologue


def test_repo_root_bootstrap_makes_shell_importable_when_run_as_a_bare_script() -> None:
    """把 sys.path 收窄到"只有 guanlan_v2/desktop 自己的目录" —— 这正是直接把
    __main__.py 当脚本跑(而非 `python -m guanlan_v2.desktop`)时 Python 会给出的
    sys.path[0]。没有仓根引导,这里 `import guanlan_v2.desktop.shell` 必炸。
    """
    driver = f"""
import sys, os
def _norm(p):
    return os.path.normcase(os.path.normpath(p))
_repo = {str(_REPO)!r}
_pkgdir = {str(_MAIN_PY.parent)!r}
sys.path[:] = [_pkgdir] + [p for p in sys.path[1:] if _norm(p) != _norm(_repo)]
_src = open({str(_MAIN_PY)!r}, encoding="utf-8").read()
_prologue = _src.split({_SENTINEL!r})[0]
exec(compile(_prologue, {str(_MAIN_PY)!r}, "exec"),
     {{"__name__": "guanlan_v2.desktop.__main__", "__file__": {str(_MAIN_PY)!r}}})
import guanlan_v2.desktop.shell  # noqa: F401 —— 引导修好了 sys.path,这句现在该成功
print("BOOTSTRAP-OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        cwd=str(_MAIN_PY.parent),  # 有意不给仓根:cwd 就是 guanlan_v2/desktop 本身
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0 and "BOOTSTRAP-OK" in proc.stdout, (
        "去掉/挪动仓根引导后,以脚本形式跑 __main__.py 会重蹈 2026-07-26 的覆辙。\n"
        f"--- returncode {proc.returncode} ---\n{proc.stderr[-3000:]}"
    )


def test_main_records_a_crash_to_disk_instead_of_vanishing_silently(tmp_path, monkeypatch) -> None:
    """双击图标后 import webview 失败或 WebView2 缺失,不该是"什么都没发生"——
    这正是引导页要防的失败模式,原因必须落盘。"""
    from guanlan_v2.desktop import __main__ as entry

    log_path = tmp_path / "crash.log"
    monkeypatch.setattr(entry, "_CRASH_LOG", log_path)

    def _boom() -> None:
        raise RuntimeError("boom, no webview2 runtime")

    rc = entry._run_and_log_crashes(_boom)
    assert rc == 1
    assert log_path.exists()
    assert "boom, no webview2 runtime" in log_path.read_text(encoding="utf-8")


def test_main_returns_zero_when_the_body_succeeds(monkeypatch) -> None:
    from guanlan_v2.desktop import __main__ as entry

    assert entry._run_and_log_crashes(lambda: None) == 0
