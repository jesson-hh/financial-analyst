# -*- coding: utf-8 -*-
"""暴露给网页的 API。无 GUID 依赖 —— 窗口工厂由 shell 注入,故可无头测试。"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from guanlan_v2.desktop.supervisor import PORT

_ALLOWED_SCHEME = "http"
_ALLOWED_HOST = "127.0.0.1"
_ALLOWED_PREFIX = "/ui/"


@dataclass(frozen=True)
class UrlVerdict:
    ok: bool
    reason: str
    detail: str


def validate_ui_url(raw) -> UrlVerdict:
    """只放行本机 9999 上的 /ui/ 页面。其余一律拒绝并说明理由。

    只认 127.0.0.1 字面量,不认 "localhost" —— 放宽会把 host 判断变成一个
    需要名字解析的开放问题,而这是安全边界,不该有开放问题。

    检查顺序 —— host 判断先于「精确 scheme」判断:对 "https://evil.example/..."
    这类 host 和 scheme 都不对的 URL,先判 scheme 会把它误判成 bad-scheme,而这里
    真正越权的地方是 host,应诊断为 bad-host;只有 host 压根解析不出来时
    (file:、javascript: 这类不透明 scheme)才在 host 判断之前截胡判 bad-scheme。
    调整这个顺序前,请把两条 parametrize 表逐行核对一遍 urlsplit 的真实输出。
    """
    if not isinstance(raw, str) or not raw.strip():
        # 非字符串输入(JS 侧传什么都可能)绝不能让 .strip()/.startswith() 炸出去。
        return UrlVerdict(False, "not-a-url", "空 URL 或非字符串输入")
    stripped = raw.strip()
    if "\\" in stripped:
        # 权威解析分歧:Python urlsplit().hostname 在最后一个 "@" 上 rpartition,
        # 反斜杠没有特殊含义;但 WebView2(Chromium/WHATWG)对 http 这类
        # "special scheme" 会把反斜杠当成 authority 的终止符 —— 于是两个解析器
        # 对同一字符串给出不同的 host,我们校验过的 host 不是最终真正被导航到的
        # host。不去猜哪个解析器"对",含反斜杠的原始 URL 一律整体拒绝。
        return UrlVerdict(False, "bad-host", "URL 含反斜杠,两种解析器会给出不同 host,一律拒绝")
    try:
        parts = urlsplit(stripped)
    except ValueError as exc:
        return UrlVerdict(False, "not-a-url", str(exc))
    if parts.hostname is None:
        # 没有可解析的 host 的 URI(file:、javascript: 这类不透明 scheme)
        # 直接判 bad-scheme —— 它们根本落不进 host 允许表的判断范围。
        return UrlVerdict(False, "bad-scheme", f"只允许 {_ALLOWED_SCHEME}:,收到 {parts.scheme or '(空)'}:")
    if parts.hostname != _ALLOWED_HOST:
        return UrlVerdict(False, "bad-host", f"只允许 {_ALLOWED_HOST},收到 {parts.hostname}")
    if parts.scheme != _ALLOWED_SCHEME:
        return UrlVerdict(False, "bad-scheme", f"只允许 {_ALLOWED_SCHEME}:,收到 {parts.scheme or '(空)'}:")
    try:
        port = parts.port
    except ValueError:
        # SplitResult.port 是惰性属性,超出 0-65535 时在**访问时**才抛
        # ValueError(urlsplit() 本身早已成功返回)。
        return UrlVerdict(False, "bad-port", "端口号超出合法范围 0-65535")
    if (port or 80) != PORT:
        return UrlVerdict(False, "bad-port", f"只允许 {PORT},收到 {port}")
    # 用 posixpath.normpath 把 "." / ".." 段折叠掉再比前缀 —— 原始字符串前缀比较
    # 看不见真实客户端(浏览器/WebView2)按 RFC 3986 §5.2.4 折叠 ".." 之后的
    # 落点,"/ui/../api/secret" 折叠后其实是 "/api/secret"。
    normalized_path = posixpath.normpath(parts.path or "/")
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if ".." in normalized_path.split("/") or not normalized_path.startswith(_ALLOWED_PREFIX):
        return UrlVerdict(False, "not-ui-path", f"只允许 {_ALLOWED_PREFIX}* ,收到 {parts.path or '(空)'}")
    return UrlVerdict(True, "", "ok")


class JsApi:
    """pywebview 把本对象的方法挂到网页的 window.pywebview.api 下。

    每个方法都返回 JSON 友好的 dict 且**从不抛异常** —— 异常穿过 JS 桥只会变成
    一个没有信息的 rejected promise,对着页面调试极其难受。
    """

    def __init__(self, *, open_window_factory: Callable[[str], None],
                 status_provider: Callable[[], dict],
                 retry_handler: Callable[[], None],
                 log_opener: Callable[[], None]) -> None:
        self._open_window_factory = open_window_factory
        self._status_provider = status_provider
        self._retry_handler = retry_handler
        self._log_opener = log_opener

    def open_window(self, url: str) -> dict:
        verdict = validate_ui_url(url)
        if not verdict.ok:
            return {"ok": False, "reason": verdict.reason, "detail": verdict.detail}
        try:
            self._open_window_factory(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "window-failed", "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "reason": "", "detail": url}

    def server_status(self) -> dict:
        try:
            return dict(self._status_provider())
        except Exception as exc:  # noqa: BLE001
            return {"state": "unknown", "detail": f"{type(exc).__name__}: {exc}"}

    def retry(self) -> dict:
        try:
            self._retry_handler()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "retry requested"}

    def open_log(self) -> dict:
        try:
            self._log_opener()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "detail": "log opened"}
