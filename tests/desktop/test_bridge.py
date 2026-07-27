# -*- coding: utf-8 -*-
"""open_window 的安全闸。

页面里渲染着 RSS 快讯与 LLM 输出 —— 那些内容我们不完全控制。原生壳里一个
不设限的开窗 API 是真实的提权面,所以这里逐条钉死放行与拒绝。
"""
from __future__ import annotations

import pytest

from guanlan_v2.desktop import bridge as br


GOOD = "http://127.0.0.1:9999/ui/screen/x.html"


@pytest.mark.parametrize("url", [
    GOOD,
    "http://127.0.0.1:9999/ui/console/a.html?embed=1",
    "http://127.0.0.1:9999/ui/seats/%E8%A7%82.html",
])
def test_allows_local_ui_pages(url):
    assert br.validate_ui_url(url).ok is True


@pytest.mark.parametrize("url,reason", [
    ("file:///C:/Windows/win.ini", "bad-scheme"),
    ("https://evil.example/ui/x.html", "bad-host"),
    ("http://evil.example/ui/x.html", "bad-host"),
    ("http://127.0.0.1:9998/ui/x.html", "bad-port"),
    ("http://127.0.0.1:9999/api/secret", "not-ui-path"),
    ("http://127.0.0.1:9999/", "not-ui-path"),
    ("http://127.0.0.1:9999/uiX/x.html", "not-ui-path"),
    ("javascript:alert(1)", "bad-scheme"),
    ("", "not-a-url"),
    ("   ", "not-a-url"),
])
def test_rejects_everything_else(url, reason):
    v = br.validate_ui_url(url)
    assert v.ok is False and v.reason == reason


def test_localhost_hostname_is_not_silently_accepted():
    # 只认 127.0.0.1 字面量;放宽会让 host 判断变成一个需要解析的开放问题
    assert br.validate_ui_url("http://localhost:9999/ui/x.html").reason == "bad-host"


# ── JsApi ──────────────────────────────────────────────────────────────
def test_open_window_calls_factory_for_allowed_url():
    opened = []
    api = br.JsApi(open_window_factory=opened.append,
                   status_provider=lambda: {"state": "healthy"},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    out = api.open_window(GOOD)
    assert out["ok"] is True and opened == [GOOD]


def test_open_window_refuses_and_never_calls_factory():
    opened = []
    api = br.JsApi(open_window_factory=opened.append,
                   status_provider=lambda: {"state": "healthy"},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    out = api.open_window("file:///C:/Windows/win.ini")
    assert out["ok"] is False and out["reason"] == "bad-scheme"
    assert opened == [], "被拒的 URL 绝不能碰到窗口工厂"


def test_open_window_survives_a_throwing_factory():
    def _boom(url):
        raise RuntimeError("no window")
    api = br.JsApi(open_window_factory=_boom,
                   status_provider=lambda: {"state": "healthy"},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    out = api.open_window(GOOD)
    assert out["ok"] is False and out["reason"] == "window-failed"


def test_server_status_passes_through():
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {"state": "degraded", "detail": "x"},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    assert api.server_status()["state"] == "degraded"


def test_retry_invokes_handler():
    calls = []
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: calls.append(1),
                   log_opener=lambda: None)
    assert api.retry()["ok"] is True and calls == [1]


def test_open_log_invokes_handler():
    calls = []
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=lambda: calls.append(1))
    assert api.open_log()["ok"] is True and calls == [1]


def test_open_log_survives_a_throwing_handler():
    def _boom():
        raise OSError("no editor")
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=_boom)
    assert api.open_log()["ok"] is False


def test_every_page_facing_method_exists():
    """网页侧只认这四个名字;boot.html / overlay.js / guanlan-nav.js 都硬编码了它们。"""
    for name in ("open_window", "server_status", "retry", "open_log"):
        assert callable(getattr(br.JsApi, name, None)), f"缺 {name}"


# ── review round 1: 四个发现的回归钉 ──────────────────────────────────────

# Critical 1 — dot-segment traversal 绕过 /ui/ 闸(RFC 3986 §5.2.4 会在
# 真实客户端里把 ".." 折叠掉,原始前缀比较看不见这个折叠后的落点)。
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9999/ui/../api/secret",
    "http://127.0.0.1:9999/ui/../../api/secret",
    "http://127.0.0.1:9999/ui/x/../../api/secret",
])
def test_dot_segment_traversal_escapes_ui_prefix_is_rejected(url):
    v = br.validate_ui_url(url)
    assert v.ok is False and v.reason == "not-ui-path"


def test_dot_segment_that_stays_inside_ui_is_still_allowed():
    # 良性的 "./"、"../" 只要折叠后仍落在 /ui/ 下,不该被误杀。
    assert br.validate_ui_url("http://127.0.0.1:9999/ui/x/../y.html").ok is True
    assert br.validate_ui_url("http://127.0.0.1:9999/ui/./x.html").ok is True


# Critical 2 — 反斜杠权威解析分歧:Python urlsplit().hostname 在 "@" 上
# rpartition,反斜杠没有特殊含义;但 WebView2(Chromium/WHATWG)对 http 这类
# "special scheme" 会把反斜杠当 authority 的终止符,导致两个解析器对同一个
# 字符串给出不同的 host —— 我们校验的 host 不是最终真正导航到的 host。
def test_backslash_before_at_defeats_authority_parsing_is_rejected():
    v = br.validate_ui_url("http://evil.example\\@127.0.0.1:9999/ui/x.html")
    assert v.ok is False and v.reason == "bad-host"


def test_plain_userinfo_variant_is_still_rejected():
    # 两个解析器在这一条上意见一致 —— 钉住防止未来重构又打开这个口子。
    v = br.validate_ui_url("http://127.0.0.1@evil.example/ui/x.html")
    assert v.ok is False and v.reason == "bad-host"


# Important 3 — validate_ui_url 对抗性输入不应抛异常(JsApi 的"从不抛异常"
# 承诺全靠它)。
@pytest.mark.parametrize("bad_input", [123, ["x"], None, 12.5, {"a": 1}])
def test_validate_ui_url_survives_non_string_input(bad_input):
    v = br.validate_ui_url(bad_input)
    assert v.ok is False and v.reason == "not-a-url"


def test_validate_ui_url_survives_port_out_of_range():
    v = br.validate_ui_url("http://127.0.0.1:99999999999999999999/ui/x.html")
    assert v.ok is False and v.reason == "bad-port"


def test_open_window_survives_non_string_input():
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    out = api.open_window(123)
    assert out["ok"] is False


def test_open_window_survives_list_input():
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    out = api.open_window(["x"])
    assert out["ok"] is False


def test_open_window_survives_oversized_port():
    api = br.JsApi(open_window_factory=lambda u: None,
                   status_provider=lambda: {},
                   retry_handler=lambda: None,
                   log_opener=lambda: None)
    out = api.open_window("http://127.0.0.1:99999999999999999999/ui/x.html")
    assert out["ok"] is False and out["reason"] == "bad-port"


# ── review round 2: 百分号编码点段(与 Critical 2 同类的分歧,已在真机 Chromium 实证)──
#
# 真机实证:把内建浏览器(Chromium,与 WebView2 同族引擎)指向
# http://127.0.0.1:9999/ui/%2e%2e/workflow/list,地址栏落点变成
# http://127.0.0.1:9999/workflow/list,响应体是 /workflow/list 的 API JSON——
# 不是 /ui 静态挂载点的 404。Chromium 的 URL 规范化器按 RFC 3986 §6.2.2.2 /
# WHATWG URL 标准,把 "%2e" 当字面的 "." 处理,再折叠点段。
@pytest.mark.parametrize("url", [
    # 与真机实证完全一致的那一条
    "http://127.0.0.1:9999/ui/%2e%2e/workflow/list",
    # 大小写变体 —— %2E 与 %2e 是同一个字符,大小写不敏感的判断不能有漏洞
    "http://127.0.0.1:9999/ui/%2E%2E/workflow/list",
    # 混合形式:一半编码一半字面
    "http://127.0.0.1:9999/ui/%2e./api/x",
    "http://127.0.0.1:9999/ui/.%2e/api/x",
])
def test_percent_encoded_dot_segment_traversal_is_rejected(url):
    v = br.validate_ui_url(url)
    assert v.ok is False and v.reason == "not-ui-path"


def test_double_percent_encoded_dot_segment_stays_literal_and_is_allowed():
    # Chromium 只解码一次:"%252e" 解一次后是字面文本 "%2e",浏览器不会把它
    # 再解成 "."。校验器同样只解码一次,所以这条落在 /ui/ 下的字面文本段
    # (对浏览器而言确实还在 /ui/ 下)不应被误杀 —— 过度拒绝不是本函数的职责,
    # "被批准的路径 == 浏览器真正会请求的路径" 才是。
    v = br.validate_ui_url("http://127.0.0.1:9999/ui/%252e%252e/workflow/list")
    assert v.ok is True


def test_approved_url_matches_the_path_a_real_browser_will_actually_request():
    """核心不变式:本函数批准的这条 URL,必须和浏览器实际会请求的路径一致——
    否则校验的是一个路径、导航的是另一个路径,闸形同虚设(这正是 Critical 2
    和这一条百分号编码发现的共同失效形状)。"""
    cases = [
        ("http://127.0.0.1:9999/ui/screen/x.html", True),       # 无编码,原样落在 /ui/ 下
        ("http://127.0.0.1:9999/ui/%2e%2e/workflow/list", False),   # 解码后落在 /ui/ 外
        ("http://127.0.0.1:9999/ui/%2E%2E/workflow/list", False),   # 同上,大小写变体
        ("http://127.0.0.1:9999/ui/%252e%252e/workflow/list", True),  # 只解一层,仍在 /ui/ 内
    ]
    for url, expected_ok in cases:
        assert br.validate_ui_url(url).ok is expected_ok, url
