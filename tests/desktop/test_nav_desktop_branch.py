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
    """挡的是「把 glDesktopApi 改成无条件返回真值」这种回归 —— 光看
    "GL_DESKTOP"/"pywebview" 是否**出现**在文件里挡不住这个,哪怕它们只活在
    注释里、跟真正的判断逻辑毫无关系,字符串检查照样通过。必须把
    glDesktopApi() 的函数体单独抠出来,断言判断逻辑真的长在里面。"""
    src = _NAV.read_text(encoding="utf-8")
    assert "open_window" in src, "桌面分支不在了"
    m = re.search(r"function glDesktopApi\(\).*?(?=function glWantsNewWindow)", src, re.S)
    assert m, "glDesktopApi 定义找不到了(函数改名或结构变了)"
    body = m.group(0)
    assert "window.pywebview && window.pywebview.api" in body, (
        "glDesktopApi 必须真的读 window.pywebview.api 才能判断桥在不在 —— "
        "不能被改成无条件返回一个真值对象(那正是本任务要防的回归)"
    )
    assert "typeof api.open_window !== 'function'" in body, (
        "glDesktopApi 必须确认 open_window 真的可调用,不能只信 api 存在就当真桥"
    )


def test_both_click_and_auxclick_are_bound():
    """click 在现代浏览器里中键不触发;只挂 click 会让中键静默失效。"""
    src = _NAV.read_text(encoding="utf-8")
    assert "'auxclick'" in src or '"auxclick"' in src
    assert "'click'" in src or '"click"' in src


def test_plain_left_click_is_not_intercepted():
    """必须有修饰键/中键判断 —— 否则普通左键点也会被 preventDefault。

    只查 "ctrlKey"/"button" 这两个词是否**出现**在文件里挡不住"删掉调用、
    留下孤儿函数"这种回归:glWantsNewWindow 的**定义**里天然就带着这两个词,
    哪怕 glOnActivate 里那句 `if (!glWantsNewWindow(e)) return;` 被删掉、
    这个函数变成谁都不调用的死代码,字符串检查照样通过。必须断言调用点本身
    ——glOnActivate 函数体里确实在最前面调了它并在其为假时早退。"""
    src = _NAV.read_text(encoding="utf-8")
    assert "ctrlKey" in src and "button" in src
    m = re.search(r"function glOnActivate\(e\).*?(?=function glBindNewWindow)", src, re.S)
    assert m, "glOnActivate 定义找不到了(函数改名或结构变了)"
    body = m.group(0)
    assert re.search(r"if\s*\(\s*!\s*glWantsNewWindow\(e\)\s*\)\s*return;", body), (
        "glOnActivate 必须在最前面调用 glWantsNewWindow(e) 并在其为假时早退 —— "
        "否则普通左键点击也会被后面的 preventDefault 拦截"
    )


def test_missing_bridge_falls_back_instead_of_breaking_navigation():
    """桥不在时不能 preventDefault,否则页面点了没反应。"""
    src = _NAV.read_text(encoding="utf-8")
    guard = re.search(r"if \(!api\)\s*return;", src)
    assert guard, "缺少「桥不在就退回普通跳转」的早退"
