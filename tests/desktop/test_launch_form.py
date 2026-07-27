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
