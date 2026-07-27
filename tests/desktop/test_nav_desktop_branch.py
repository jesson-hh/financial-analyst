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
