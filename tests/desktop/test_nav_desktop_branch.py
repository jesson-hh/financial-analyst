# -*- coding: utf-8 -*-
"""顶栏被十二个页面共用,不是八个 —— MODULES 只列了 8 个 tab,但
cards/chat/factor/graph 四个页面仍在加载同一份 guanlan-nav.js(2026-06-12
phase2 收敛"隐藏不删":从 tab 条隐去,<script> 标签留着,见
docs/superpowers/plans/2026-06-12-weiwo-console-phase2.md:777)。浏览器也吃
同一份 —— 页面越多,这段没条件化的代价就越大,所以桌面分支必须是条件化的。

仓库没有前端测试设施,本文件只做源码级守卫:挡住「无条件开新窗」这类会
弄坏浏览器行为的改法。真行为验证在 Step 4 用真浏览器做。
"""
from __future__ import annotations

import re
from pathlib import Path

_NAV = Path(__file__).resolve().parents[2] / "ui" / "_shared" / "guanlan-nav.js"


def test_embed_guard_is_still_the_very_first_thing():
    """?embed=1 提前 return 是帷幄右栏 iframe 的嵌入卫生,不能被挤到后面。

    只查 "embed" 这个词第一次出现在文件哪里挡不住"删掉真正的 return 语句、
    只留下上面解释性注释"这种回归——文件里第一处 "embed" 是 guanlan-nav.js:4
    的注释,不是 :5 那句真正的早退;`src.index("embed")` 两种情况下指向同一个
    位置,删掉 :5 这条测试原样通过。必须断言真的存在"取 embed 参数 =='1' 就
    return"这条语句本身,并且它出现在 MODULES 定义之前。"""
    src = _NAV.read_text(encoding="utf-8")
    m = re.search(r"URLSearchParams\(location\.search\)\.get\('embed'\)\s*===\s*'1'\)\s*return;", src)
    assert m, "embed 早退语句找不到了(被删除,或被改写成别的形态)"
    assert m.start() < src.index("MODULES"), "embed 卫生必须在建导航之前"


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
    assert re.search(r"return null;", body), (
        "glDesktopApi 在桥不在/不可调用时必须真的 return null —— 只查上面两个"
        "子串是否**读到**桥挡不住把「否则」分支换成一个真值 stub 这种回归"
        "(比如 `return { open_window: function () {} };`):两个子串照样原样"
        "出现,但 glOnActivate 会把这个 stub 当成真桥,吞掉十二个页面上所有的"
        "ctrl-click/中键点击"
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
