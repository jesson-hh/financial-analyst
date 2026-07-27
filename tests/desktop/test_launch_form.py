# -*- coding: utf-8 -*-
"""快捷方式的启动形态守卫。

2026-07-26 晚 9999 起不来,根因是生产以**脚本**形式跑 server.py,sys.path[0]
成了包目录、仓根不在路径上,而全套件绿(测试与 `-m` 都自带仓根)。桌面壳是
一个新的生产启动点,这里把它钉在 `-m` 形态上。同类守卫见
tests/test_server_script_launch.py。

注:`test_shortcut_launches_via_dash_m_not_a_script_path` 是文本级 grep,不是像
tests/test_server_script_launch.py 那样的行为测试——它能拦住最可能的字面回归
(改回一条脚本路径),但拦不住一条动态拼出来的路径(比如拼字符串绕开
"-m guanlan_v2.desktop" 这个字面量)。
"""
from __future__ import annotations

import re
from pathlib import Path

_PS1 = Path(__file__).resolve().parents[2] / "scripts" / "install_desktop_shortcut.ps1"
_ICON = Path(__file__).resolve().parents[2] / "guanlan_v2" / "desktop" / "guanlan.ico"


def test_shortcut_launches_via_dash_m_not_a_script_path():
    """只查字面量 "-m guanlan_v2.desktop" 是否**出现**在文件里挡不住:
    install_desktop_shortcut.ps1:9 的头部注释原样含有这个字面量(「Launch form
    is `-m guanlan_v2.desktop` ...」),把真正的 `$lnk.Arguments = '-m
    guanlan_v2.desktop'` 赋值删掉、只留下头部这行解释性注释,这条测试照样
    通过。第二条断言同理拦不住一条拼出来的脚本路径,比如
    `Join-Path $Repo 'guanlan_v2' 'desktop' '__main__.py'` 再赋给
    `$lnk.Arguments`——这正是这个守卫本该拦住的、2026-07-26 那次停机的启动
    形态,两条字面量断言都会对它视而不见。必须断言 `$lnk.Arguments` 这条赋值
    语句本身,同 test_shortcut_working_directory_is_the_repo_root 的做法一致。"""
    src = _PS1.read_text(encoding="utf-8-sig")
    assert re.search(r"\$lnk\.Arguments\s*=\s*'-m guanlan_v2\.desktop'", src), (
        "$lnk.Arguments must be assigned the literal '-m guanlan_v2.desktop' -- "
        "a mention in a comment elsewhere in the file is not enough"
    )
    assert "desktop\\__main__.py" not in src and "desktop/__main__.py" not in src


def test_shortcut_uses_pythonw_so_no_console_window_flashes():
    assert "pythonw.exe" in _PS1.read_text(encoding="utf-8-sig")


def test_shortcut_working_directory_is_the_repo_root():
    """不只检查 "WorkingDirectory" 和 "guanlan-v2" 这两个子串各自出现在文件
    某处(那样两处互不相干的巧合也能骗过去)——而是断言两者真的被接在一起:
    `$lnk.WorkingDirectory` 被赋值成 `$Repo`,且 `$Repo` 这个变量本身被赋值成
    一条含 "guanlan-v2" 的字面路径。"""
    src = _PS1.read_text(encoding="utf-8-sig")
    assert re.search(r"\$lnk\.WorkingDirectory\s*=\s*\$Repo\b", src), (
        "WorkingDirectory must be assigned from $Repo, not some other value"
    )
    repo_assignment = re.search(r"\$Repo\s*=\s*'([^']*)'", src)
    assert repo_assignment is not None, "$Repo must be assigned a literal path"
    assert "guanlan-v2" in repo_assignment.group(1)


def test_ps1_saved_as_utf8_with_bom():
    """这是本任务要守住的头号性质,此前没有任何测试断言过它。

    此文件所有测试此前都用 `encoding="utf-8-sig"` 读取——这个编码名无论文件
    开头有没有 BOM 都能正确解码(有 BOM 就吃掉它,没有就照常读),所以就算
    有人以后在纯文本编辑器里打开这个文件、原样存成不带 BOM 的 UTF-8,
    整套测试也不会有任何一个变红。而现实后果是 PowerShell 5.1 会把不带 BOM
    的 UTF-8 当 ANSI 读——这个文件里的中文字符串字面量(尤其是快捷方式显示名
    观澜)要么变成乱码,要么直接把脚本解析炸掉。所以 BOM 本身必须被断言,
    不能只靠"能被 utf-8-sig 解码"这种和有没有 BOM 无关的间接证据。
    """
    assert _PS1.read_bytes()[:3] == b"\xef\xbb\xbf", (
        "install_desktop_shortcut.ps1 must be saved as UTF-8 WITH BOM -- "
        "PS 5.1 reads BOM-less UTF-8 as ANSI, and this file carries a CJK "
        "string literal (the shortcut display name)"
    )


def _extract_ps1_comment(line: str) -> str | None:
    """返回这一行里"未被引号包住的第一个 # 起,到行尾"的子串(即注释本体),
    如果这一行压根没有那样的 # 就返回 None。

    这里必须先扫过 PowerShell 的单引号/双引号字符串字面量再找 `#`,否则字符
    串里恰好带的一个 `#`(比如一个十六进制颜色、或者显示文本里的话题标签)
    会被误判成注释起点——那是假阳性,不是这个测试要抓的东西。单引号字符串
    用连续两个单引号 `''` 转义引号本身;双引号字符串用反引号 `` ` `` 转义
    下一个字符。这个文件今天没有任何字符串字面量含 `#`,所以这条转义/引号
    追踪路径目前是防御性的、没有被现有内容真正跑到过——如果以后这个文件里
    出现一条真的含 `#` 的字符串字面量,这个函数会把它当非注释跳过,不会误报。
    """
    in_single = False
    in_double = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_single:
            if c == "'":
                if i + 1 < n and line[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "`" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue
        if c == "#":
            return line[i:]
        i += 1
    return None


def test_ps1_comments_are_ascii_only():
    """PS 5.1 把无 BOM UTF-8 的非 ASCII 注释当 ANSI 读 → 语法炸(memory 大坑③)。

    同时也要抓行尾的附加注释(比如 `$x = 1  # 说明`),不能只看整行都是注释
    的情形——一条附加注释里出现非 ASCII 字符是同样的风险,只是这个文件今天
    恰好没有任何附加注释,所以这条路径是防御性覆盖。见 `_extract_ps1_comment`
    对字符串字面量转义的处理与其局限。
    """
    for i, line in enumerate(_PS1.read_text(encoding="utf-8-sig").splitlines(), 1):
        comment = _extract_ps1_comment(line)
        if comment is not None:
            assert comment.isascii(), f"line {i}: non-ASCII comment: {comment!r}"


def test_icon_exists_and_is_an_ico():
    assert _ICON.exists(), "图标未生成"
    assert _ICON.read_bytes()[:4] == b"\x00\x00\x01\x00", "不是 ICO 文件头"


_EXPECTED_ICON_SIZES = {16, 24, 32, 48, 64, 128, 256}
_MIN_PLAUSIBLE_PAYLOAD_BYTES = 200  # 真实最小帧(16x16)实测 684 字节,留出安全边际


def _parse_ico_directory(data: bytes) -> list[tuple[int, int, int]]:
    """纯 Python 解析 ICO 的目录表(ICONDIR + 若干 ICONDIRENTRY),只读结构,
    不解码像素。返回每帧的 (width, height, byte_size_in_file)。"""
    count = int.from_bytes(data[4:6], "little")
    entries = []
    for i in range(count):
        off = 6 + i * 16
        entry = data[off : off + 16]
        width = entry[0] or 256  # 0 在 ICO 目录里表示 256
        height = entry[1] or 256
        byte_size = int.from_bytes(entry[8:12], "little")
        entries.append((width, height, byte_size))
    return entries


def test_icon_has_all_expected_sizes_with_plausible_payloads():
    """结构性检查:确认 ICO 目录里 7 个预期尺寸都在、且每帧声明的字节数
    过了一个看似合理的下限(不是空/被截断的图)。

    诚实声明这个测试**验证不了**的东西:它不解码像素,不知道图是不是朱红色、
    「觀」字是不是真的画上去了——一次字体回退 bug 产的一张同样尺寸、同样文件
    大小量级的纯色空白图,这个测试照样会绿。要抓住那一类问题需要真的解码
    像素(比如借助 pillow),而这个仓库的约定是 pillow 不进任何运行时/测试
    依赖,所以这里刻意只做结构级校验。"""
    entries = _parse_ico_directory(_ICON.read_bytes())
    sizes = {w for w, h, _ in entries}
    assert sizes == _EXPECTED_ICON_SIZES, f"unexpected size set: {sizes}"
    for width, height, byte_size in entries:
        assert byte_size > _MIN_PLAUSIBLE_PAYLOAD_BYTES, (
            f"{width}x{height} payload suspiciously small ({byte_size} bytes) "
            "-- looks like a truncated or empty regeneration"
        )
