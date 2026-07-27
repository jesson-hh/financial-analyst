# -*- coding: utf-8 -*-
"""引导页与浮层是壳靠 evaluate_js 驱动的,契约就是那几个全局函数名。

没有前端测试设施,所以这里只钉「壳依赖的名字确实存在、且资源自包含」——
弱,但能挡住重命名和外链。真正的行为验证在 Task 6 的浏览器实测与人工验收。
"""
from __future__ import annotations

import re
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


_CALL_RE = re.compile(r"""glCallApi\(\s*['"](\w+)['"]\s*\)""")


def test_both_assets_only_call_methods_that_JsApi_actually_has():
    """两处按钮硬编码了 API 名字;名字对不上就是一个点了没反应的按钮,而且没有

    任何 Python 测试会发现 —— 契约跨了语言边界。这条测试把它钉住。

    **本测试绝不允许空跑。** 资产里对 Python 侧的调用必须写成
    ``glCallApi('<字面量方法名>')``;抽不到名字就是资产改了调用形式,那时这条
    测试会因为 0 个名字而**失败**,而不是悄悄通过。这一点本身就是被下面第一条
    断言保护的。
    """
    from guanlan_v2.desktop.bridge import JsApi

    found: dict[str, set[str]] = {}
    for name in ("boot.html", "overlay.js"):
        src = (_DIR / name).read_text(encoding="utf-8")
        found[name] = set(_CALL_RE.findall(src))
        assert found[name], (
            f"{name} 里一个 glCallApi('...') 都没抽到 —— 要么按钮没了,"
            "要么有人把调用改成了正则看不见的形式(a.retry() / api[m]()),"
            "那样这条跨语言契约测试就成了空跑。"
        )
        for method in found[name]:
            assert hasattr(JsApi, method), f"{name} 调了 JsApi 没有的 {method}()"

    # 两个按钮的方法都必须真的被两处各自引用到
    for name in ("boot.html", "overlay.js"):
        assert found[name] == {"retry", "open_log"}, (
            f"{name} 抽到 {sorted(found[name])},预期恰好 retry 与 open_log"
        )
