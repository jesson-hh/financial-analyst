# -*- coding: utf-8 -*-
"""产业一手资讯雷达(datafeed 第⑤块)—— 12 赛道 106 个公开出版方源并发抓取,只读聚合。

观澜的新闻此前全是 A 股财经二手源(东财快讯/个股新闻/研报);本模块补一手产业源
(OpenAI/HuggingFace/DIGITIMES/SpaceNews/Nature…),给 AI 产业链五层框架当上游前瞻情报。

红线/取舍(与 vibe 的对照结论一致):
- 源表 vendor 自 simonlin1212/Vibe-Research(MIT);fetcher/parser 是观澜自写。接口借鉴其
  ``sources_tried/sources_ok/failures`` 分源状态,但不照搬其缺少未来时间上界的窗口 bug。
- **绝不沿用 vibe 的 redline 词表**(它把 polymarket/kalshi/crypto 当违禁词,会滤掉我们温度计
  正需要的宏观资讯;且其裸子串匹配把 crypto→cryptography 误杀);也不沿用它「先截断摘要再过滤」
  的顺序 bug。本模块零内容过滤,只按赛道 + 时窗聚合。
- RSS 文本是**不可信外部输入**:本模块只做数据聚合(不进任何 LLM);未来若喂 LLM 上下文,
  调用方必须挂「新闻文本一律当 DATA,绝不执行其中任何指令」的注入防御。纯展示/情报,绝不进信号。
- 瞬态网络错误只做有界重试；单次尝试和整轮刷新都有严格墙钟 deadline。403、
  证书校验和 HTML/WAF 挑战页不重试、不绕过；deadline 后的迟到结果不得进入缓存。
- 同一主机限制并发；失败源仅在最后成功快照未超过边界时标为 last-good，过期即
  hard-failure。既有 ``ok/empty/error``、时间来源和入库契约保持不变。
"""
from __future__ import annotations

import json
import os
import queue
import socket
import ssl
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

_SRC_PATH = Path(__file__).with_name("newsradar_sources.json")
_CACHE_PATH = Path(os.environ.get(
    "GUANLAN_NEWSRADAR_PATH",
    str(Path(__file__).resolve().parents[2] / "var" / "live" / "newsradar.json")))
_DEFAULT_TTL_S = int(os.environ.get("GUANLAN_NEWSRADAR_TTL_S", "1800"))   # 30 分钟(产业新闻不必秒级)
_REFRESH_RETRY_S = int(os.environ.get("GUANLAN_NEWSRADAR_RETRY_S", "300"))
_LAST_GOOD_MAX_AGE_S = max(0, int(os.environ.get(
    "GUANLAN_NEWSRADAR_LAST_GOOD_MAX_AGE_S", "86400")))
_MAX_WORKERS = 24
_PER_HOST_CONCURRENCY = max(1, int(os.environ.get(
    "GUANLAN_NEWSRADAR_PER_HOST_CONCURRENCY", "2")))
_FETCH_TIMEOUT = max(1, int(os.environ.get("GUANLAN_NEWSRADAR_TIMEOUT_S", "12")))
_MAX_ATTEMPTS = max(1, int(os.environ.get("GUANLAN_NEWSRADAR_MAX_ATTEMPTS", "3")))
_BACKOFF_BASE_S = max(0.0, float(os.environ.get(
    "GUANLAN_NEWSRADAR_BACKOFF_BASE_S", "0.25")))
_BACKOFF_MAX_S = max(_BACKOFF_BASE_S, float(os.environ.get(
    "GUANLAN_NEWSRADAR_BACKOFF_MAX_S", "2.0")))
_REFRESH_DEADLINE_S = max(1.0, float(os.environ.get(
    "GUANLAN_NEWSRADAR_REFRESH_DEADLINE_S", "45")))
_HTTP_ATTEMPT_WORKERS = max(1, int(os.environ.get(
    "GUANLAN_NEWSRADAR_HTTP_ATTEMPT_WORKERS", str(_MAX_WORKERS))))
_HTTP_ATTEMPT_QUEUE = max(1, int(os.environ.get(
    "GUANLAN_NEWSRADAR_HTTP_ATTEMPT_QUEUE", str(_HTTP_ATTEMPT_WORKERS))))
_HTTP_CLOSE_WORKERS = max(1, int(os.environ.get(
    "GUANLAN_NEWSRADAR_HTTP_CLOSE_WORKERS", "4")))
_HTTP_CLOSE_QUEUE = max(1, int(os.environ.get(
    "GUANLAN_NEWSRADAR_HTTP_CLOSE_QUEUE", str(_HTTP_CLOSE_WORKERS))))
_CACHE_LOCK_TIMEOUT_S = max(0.1, float(os.environ.get(
    "GUANLAN_NEWSRADAR_CACHE_LOCK_TIMEOUT_S", "5")))
_PRIORITY_SECTORS = tuple(value.strip() for value in os.environ.get(
    "GUANLAN_NEWSRADAR_PRIORITY_SECTORS", "semi,ai,robot,energy,bio"
).split(",") if value.strip())
_MAX_ITEMS_PER_SOURCE = 20
# RSS is read through a bounded stream.  The first 8 KiB is enough to reject
# ordinary HTML/WAF documents before allocating the rest, while the total cap
# prevents an allegedly successful endpoint from returning an unbounded body.
_WAF_PREFIX_BYTES = 8 * 1024
_MAX_FEED_BYTES = 16 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
_CACHE_SCHEMA_VERSION = 2

_LOCK = threading.Lock()
_WARM_LOCK = threading.Lock()
_WARM_INFLIGHT = [False]


class _InvalidFeedError(ValueError):
    """HTTP 成功但正文不是可识别的 RSS/Atom。"""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = bool(retryable)


class _AttemptDeadlineError(TimeoutError):
    """单次 HTTP 尝试超过严格墙钟边界。"""


class _RefreshDeadlineError(TimeoutError):
    """整轮刷新截止；未完成源必须停止参与本轮快照。"""


class _BoundedDaemonPool:
    """固定 daemon worker + 有界队列；第三方永久阻塞也不能跨轮增殖线程。"""

    def __init__(self, workers: int, queue_size: int, name: str):
        self.workers = max(1, int(workers))
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max(1, int(queue_size)))
        self._name = name
        self._start_lock = threading.Lock()
        self._threads: List[threading.Thread] = []

    def _ensure_started(self) -> None:
        if len(self._threads) == self.workers:
            return
        with self._start_lock:
            while len(self._threads) < self.workers:
                index = len(self._threads) + 1
                thread = threading.Thread(
                    target=self._worker,
                    name=f"{self._name}_{index}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def _worker(self) -> None:
        while True:
            operation = self._queue.get()
            try:
                operation()
            except BaseException:  # noqa: BLE001 - worker 必须持续服务后续任务
                pass
            finally:
                self._queue.task_done()

    def submit_until(self, operation, deadline: float,
                     cancel_event: Optional[threading.Event] = None) -> bool:
        self._ensure_started()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                self._queue.put(operation, timeout=min(0.05, remaining))
                return True
            except queue.Full:
                continue

    def try_submit(self, operation) -> bool:
        self._ensure_started()
        try:
            self._queue.put_nowait(operation)
            return True
        except queue.Full:
            return False


_HTTP_ATTEMPT_POOL = _BoundedDaemonPool(
    _HTTP_ATTEMPT_WORKERS, _HTTP_ATTEMPT_QUEUE, "newsradar_http_attempt")
_HTTP_CLOSE_POOL = _BoundedDaemonPool(
    _HTTP_CLOSE_WORKERS, _HTTP_CLOSE_QUEUE, "newsradar_http_close")


def _local(tag: str) -> str:
    """去命名空间取本地标签名(Atom `{http://www.w3.org/2005/Atom}entry` → `entry`)。"""
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _parse_dt(raw: str) -> Optional[int]:
    """RFC822/ISO8601 且明确带时区 → epoch 秒;失败或无时区返回 None。

    Vibe-Research 的普通 RSS 路径会把无时区值静默当 UTC；其更严格的 Techmeme
    路径已经证明这种做法会制造错时。NewsRadar 保留条目但把这类时间归到 unknown，
    不让它进入“近 N 天”窗口。
    """
    raw = " ".join((raw or "").split())
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)               # RFC822
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return None
    return int(dt.timestamp())


def _iso_utc(ts: Any) -> str:
    try:
        value = int(ts)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(
        microsecond=0,
    ).isoformat().replace("+00:00", "Z")


def _parse_feed_root(root: ET.Element, source_name: str, sector: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        d = {"title": "", "url": "", "summary": "", "source": source_name,
             "sector": sector, "ts": 0}
        raw_published = ""
        raw_updated = ""
        raw_generic_date = ""
        published_tag = ""
        for c in node:
            t = _local(c.tag)
            if t == "title" and not d["title"]:
                d["title"] = (c.text or "").strip()
            elif t == "link" and not d["url"]:
                d["url"] = c.get("href") or (c.text or "").strip()   # Atom 取 href 属性,RSS 取正文
            elif t in ("pubdate", "published") and not raw_published:
                raw_published = (c.text or "").strip()
                published_tag = t
            elif t == "date" and not raw_generic_date:
                raw_generic_date = (c.text or "").strip()
            elif t == "updated" and not raw_updated:
                raw_updated = (c.text or "").strip()
            elif t in ("description", "summary", "content") and not d["summary"]:
                d["summary"] = _strip_html(c.text or "")[:280]
        if not d["title"]:
            continue
        published_ts = _parse_dt(raw_published)
        updated_ts = _parse_dt(raw_updated)
        generic_ts = _parse_dt(raw_generic_date)
        rawtime = (raw_published if published_ts else raw_updated if updated_ts
                   else raw_generic_date if generic_ts else
                   raw_published or raw_updated or raw_generic_date)
        time_tag = (published_tag if published_ts else "updated" if updated_ts
                    else "date" if generic_ts else "")
        ts = published_ts or updated_ts or generic_ts
        d["ts"] = ts or 0
        d["time"] = (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M") if ts else "—")
        d["source_time"] = _iso_utc(ts)
        d["source_time_raw"] = rawtime
        d["time_basis"] = ("updated" if time_tag == "updated" else
                           "published" if time_tag in ("pubdate", "published") else
                           "unknown") if ts else "unknown"
        d["published_at"] = _iso_utc(published_ts) if published_tag in ("pubdate", "published") else ""
        d["updated_at"] = _iso_utc(updated_ts)
        d["time_status"] = "verified_source_time" if ts else "undated"
        d["is_undated"] = not bool(ts)
        d["undated_reason"] = ("" if ts else
                               "source_time_unparseable_or_timezone_missing"
                               if rawtime else "source_omitted_time")
        out.append(d)
    return out


def _parse_feed(content: str, source_name: str, sector: str) -> List[Dict[str, Any]]:
    """RSS 2.0/Atom 解析；无标题条目丢弃，非法 XML 兼容返回空。绝不做内容过滤。"""
    try:
        root = ET.fromstring(content.encode("utf-8") if isinstance(content, str) else content)
    except ET.ParseError:
        return []
    return _parse_feed_root(root, source_name, sector)


def _parse_feed_checked(content: Any, source_name: str, sector: str) -> List[Dict[str, Any]]:
    """抓取路径用的严格解析：XML 非法必须进入逐源 failure，不能伪装成合法空源。"""
    root = ET.fromstring(content.encode("utf-8") if isinstance(content, str) else content)
    if _local(root.tag) not in ("rss", "feed", "rdf"):
        raise _InvalidFeedError(
            "response is not a recognized RSS/Atom document",
            retryable=not _is_html_document(content),
        )
    return _parse_feed_root(root, source_name, sector)


def _parse_wallstreetcn_information_flow(
        content: Any, source_name: str, sector: str) -> List[Dict[str, Any]]:
    """Parse the public JSON endpoint used by wallstreetcn.com's own web app.

    The upstream RSS URL remains live but returns an HTML challenge to this
    runtime.  This adapter only accepts ordinary article resources from the
    publisher's versioned ``/apiv1/content/information-flow`` response; live
    flashes and chart/carousel insertions are deliberately excluded so the
    replacement keeps the article-feed semantics.
    """
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise _InvalidFeedError(
            f"invalid WallstreetCN JSON: {str(exc)[:120]}", retryable=True,
        ) from exc
    if (not isinstance(payload, dict)
            or payload.get("code") not in (20000, "20000")):
        raise _InvalidFeedError(
            "unexpected WallstreetCN API envelope", retryable=False,
        )
    data = payload.get("data")
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise _InvalidFeedError(
            "WallstreetCN API response has no item list", retryable=False,
        )

    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("resource_type") != "article":
            continue
        resource = row.get("resource")
        if not isinstance(resource, dict):
            continue
        url = str(resource.get("uri") or "").strip()
        # The home information-flow can inject a chart under an ``article``
        # request.  Keep only canonical publisher article URLs.
        if not url.startswith("https://wallstreetcn.com/articles/"):
            continue
        title = str(resource.get("title") or "").strip()
        if not title:
            continue
        try:
            ts = int(resource.get("display_time") or 0)
        except (TypeError, ValueError, OverflowError):
            ts = 0
        if ts > 10_000_000_000:  # tolerate a future millisecond representation
            ts //= 1000
        source_time = _iso_utc(ts)
        summary = _strip_html(str(
            resource.get("content_text") or resource.get("content") or ""
        ))[:280]
        out.append({
            "title": title,
            "url": url,
            "summary": summary,
            "source": source_name,
            "sector": sector,
            "ts": ts if source_time else 0,
            "time": (datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")
                     if source_time else "—"),
            "source_time": source_time,
            "source_time_raw": str(resource.get("display_time") or ""),
            "time_basis": "published" if source_time else "unknown",
            "published_at": source_time,
            "updated_at": "",
            "time_status": "verified_source_time" if source_time else "undated",
            "is_undated": not bool(source_time),
            "undated_reason": ("" if source_time else
                               "source_time_unparseable_or_timezone_missing"
                               if resource.get("display_time") else "source_omitted_time"),
        })
    return out


def _is_html_document(content: Any, content_type: str = "") -> bool:
    """只识别文档根 HTML/WAF 页，避免把 RSS 的 CDATA HTML 摘要误判成挑战页。"""
    if "text/html" in str(content_type or "").lower():
        return True
    sample = (bytes(content[:_WAF_PREFIX_BYTES]).lower()
              if isinstance(content, (bytes, bytearray))
              else str(content or "")[:_WAF_PREFIX_BYTES].lower().encode(
                  "utf-8", errors="ignore"))
    sample = sample.lstrip()
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:].lstrip()
    while True:
        if sample.startswith(b"<?"):
            end = sample.find(b"?>")
            if end < 0:
                break
            sample = sample[end + 2:].lstrip()
            continue
        if sample.startswith(b"<!--"):
            end = sample.find(b"-->")
            if end < 0:
                break
            sample = sample[end + 3:].lstrip()
            continue
        break
    if sample.startswith(b"<!doctype html"):
        return True
    if not sample.startswith(b"<html"):
        return False
    following = sample[5:6]
    return not following or following in b" \t\r\n>/"


def _is_waf_or_challenge_response(content: Any, content_type: str = "") -> bool:
    """HTTP 错误响应若是 HTML 或典型挑战页，绝不按 429/5xx 瞬态重试。"""
    if _is_html_document(content, content_type):
        return True
    sample = (bytes(content[:8192]).lower() if isinstance(content, (bytes, bytearray))
              else str(content or "")[:8192].lower().encode("utf-8", errors="ignore"))
    return any(marker in sample for marker in (
        b"cloudflare", b"cf-chl-", b"challenge-platform", b"captcha",
        b"attention required", b"just a moment", b"access denied", b"web application firewall",
    ))


def _looks_like_feed_prefix(content: Any) -> bool:
    """Recognise an XML feed root without parsing an intentionally partial body."""
    sample = (bytes(content[:_WAF_PREFIX_BYTES]).lower()
              if isinstance(content, (bytes, bytearray))
              else str(content or "")[:_WAF_PREFIX_BYTES].lower().encode(
                  "utf-8", errors="ignore"))
    sample = sample.lstrip()
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:].lstrip()
    # XML declarations, processing instructions and leading comments are legal.
    # Work only inside the already-bounded prefix and stop on an incomplete one.
    while True:
        if sample.startswith(b"<?"):
            end = sample.find(b"?>")
            if end < 0:
                return False
            sample = sample[end + 2:].lstrip()
            continue
        if sample.startswith(b"<!--"):
            end = sample.find(b"-->")
            if end < 0:
                return False
            sample = sample[end + 3:].lstrip()
            continue
        break
    return any(sample.startswith(root) for root in (
        b"<rss", b"<feed", b"<rdf:rdf", b"<rdf",
    ))


def _response_content_length(response: Any) -> Optional[int]:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    try:
        raw = str(getter("Content-Length") or "").strip()
        value = int(raw) if raw else None
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value is not None and value >= 0 else None


def _as_response_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode("utf-8")
    raise _InvalidFeedError("response body is not bytes", retryable=False)


def _read_feed_response_bounded(response: Any, content_type: str) -> bytes:
    """Read one successful response with early WAF and total-size boundaries.

    ``urllib`` responses support ``read(size)``.  The no-argument fallback is
    retained solely for existing injected/test response objects; it is still
    size-checked immediately and remains inside the strict wall-clock worker.
    """
    if _is_html_document(b"", content_type):
        raise _InvalidFeedError("HTML/challenge response", retryable=False)
    declared = _response_content_length(response)
    if declared is not None and declared > _MAX_FEED_BYTES:
        raise _InvalidFeedError(
            f"response exceeds {_MAX_FEED_BYTES} byte limit", retryable=False,
        )
    reader = getattr(response, "read", None)
    if not callable(reader):
        raise _InvalidFeedError("response body is unreadable", retryable=False)

    try:
        first_raw = reader(_WAF_PREFIX_BYTES)
    except TypeError:
        # Backwards-compatible seam for the tiny response doubles used by the
        # offline suite.  Real urllib HTTPResponse.read accepts a size.
        raw = _as_response_bytes(reader())
        prefix = raw[:_WAF_PREFIX_BYTES]
        xml_type = any(token in str(content_type or "").lower()
                       for token in ("xml", "rss", "atom"))
        if _is_html_document(prefix, content_type) or (
                not xml_type and not _looks_like_feed_prefix(prefix)
                and _is_waf_or_challenge_response(prefix, content_type)):
            raise _InvalidFeedError("HTML/challenge response", retryable=False)
        if len(raw) > _MAX_FEED_BYTES:
            raise _InvalidFeedError(
                f"response exceeds {_MAX_FEED_BYTES} byte limit", retryable=False,
            )
        return raw
    first = _as_response_bytes(first_raw)

    if len(first) > _WAF_PREFIX_BYTES:  # hostile/non-conforming reader
        raise _InvalidFeedError(
            "response reader exceeded requested prefix", retryable=False,
        )
    xml_type = any(token in str(content_type or "").lower()
                   for token in ("xml", "rss", "atom"))
    if _is_html_document(first, content_type) or (
            not xml_type and not _looks_like_feed_prefix(first)
            and _is_waf_or_challenge_response(first, content_type)):
        raise _InvalidFeedError("HTML/challenge response", retryable=False)

    chunks = [first]
    total = len(first)
    while total <= _MAX_FEED_BYTES:
        chunk = _as_response_bytes(reader(min(
            _READ_CHUNK_BYTES, _MAX_FEED_BYTES + 1 - total,
        )))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
    raise _InvalidFeedError(
        f"response exceeds {_MAX_FEED_BYTES} byte limit", retryable=False,
    )


def _classify_error(exc: BaseException) -> tuple[str, Optional[int], bool]:
    """返回 ``(error_kind, http_status, retryable)``；证书/WAF 永不绕过。"""
    import http.client
    import urllib.error

    if isinstance(exc, _RefreshDeadlineError):
        return "refresh_deadline", None, False
    if isinstance(exc, _AttemptDeadlineError):
        return "timeout", None, True
    if isinstance(exc, urllib.error.HTTPError):
        code = int(exc.code)
        return f"http_{code}", code, code in (408, 425, 429) or 500 <= code <= 599
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _classify_error(reason)
        low = str(reason or "").lower().replace("_", " ")
        if "certificate verify" in low or "cert verify" in low:
            return "tls_cert_verification", None, False
        if "unexpected eof" in low or "eof occurred" in low:
            return "tls_eof", None, True
        if "timed out" in low or "timeout" in low:
            return "timeout", None, True
        return "url_error", None, True
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls_cert_verification", None, False
    if isinstance(exc, ssl.SSLEOFError):
        return "tls_eof", None, True
    if isinstance(exc, ssl.SSLError):
        low = str(exc).lower().replace("_", " ")
        if "certificate verify" in low or "cert verify" in low or "hostname" in low:
            return "tls_cert_verification", None, False
        if "unexpected eof" in low or "eof occurred" in low:
            return "tls_eof", None, True
        return "tls_error", None, False
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", None, True
    if isinstance(exc, (http.client.IncompleteRead, http.client.RemoteDisconnected,
                        ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                        ConnectionRefusedError, EOFError)):
        return "connection_error", None, True
    if isinstance(exc, _InvalidFeedError):
        return "invalid_feed", None, exc.retryable
    if isinstance(exc, ET.ParseError):
        return "invalid_feed", None, True
    if isinstance(exc, OSError):
        return "network_error", None, True
    return type(exc).__name__.lower(), None, False


def _response_content_type(response: Any) -> str:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return ""
    try:
        return str(getter("Content-Type") or "")[:120]
    except Exception:  # noqa: BLE001 - 不可信响应头只作审计元数据
        return ""


def _parse_iso_ts(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _source_host(source: Dict[str, Any]) -> str:
    try:
        return (urlsplit(str(source.get("url") or "")).hostname or "").lower()
    except ValueError:
        return ""


def _priority_rank(source: Dict[str, Any]) -> int:
    sector = str(source.get("hint") or source.get("sector") or "")
    try:
        return _PRIORITY_SECTORS.index(sector)
    except ValueError:
        return len(_PRIORITY_SECTORS)


def load_sources():
    """(industries, sources) — vendored 源表(simonlin1212/Vibe-Research·MIT)。"""
    data = json.loads(_SRC_PATH.read_text(encoding="utf-8"))
    return data.get("industries") or [], data.get("sources") or []


def _close_inflight_response(state: Dict[str, Any]) -> None:
    response = state.get("response")
    closer = getattr(response, "close", None)
    if not callable(closer):
        return
    # deadline、HTTPError 检查等路径可能同时请求关闭；每个响应最多入队一次。
    if state.get("close_scheduled"):
        return
    state["close_scheduled"] = True

    def _close() -> None:
        try:
            closer()
        except Exception:  # noqa: BLE001 - 取消时 best-effort 关闭 socket
            pass

    # ``close()`` 也来自第三方响应对象，放入固定 worker + 有界队列。队列饱和时
    # 宁可跳过 best-effort close，也不能为不可控第三方代码再增殖线程/任务。
    _HTTP_CLOSE_POOL.try_submit(_close)


def _strict_wall_clock_call(operation, *, timeout_s: float,
                            round_cancel: Optional[threading.Event],
                            round_deadline: Optional[float]) -> Any:
    """固定有界 worker 中执行第三方 I/O；超时结果永不回流聚合器。"""
    local_cancel = threading.Event()
    done = threading.Event()
    state: Dict[str, Any] = {}
    result_box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            if local_cancel.is_set() or (round_cancel is not None and round_cancel.is_set()):
                return
            result_box["value"] = operation(local_cancel, state)
        except Exception as exc:  # noqa: BLE001 - 在调用线程按原分类重抛
            result_box["error"] = exc
        finally:
            done.set()

    started = time.monotonic()
    attempt_deadline = started + max(0.01, float(timeout_s))
    effective_deadline = (min(attempt_deadline, round_deadline)
                          if round_deadline is not None else attempt_deadline)
    deadline_is_round = bool(
        round_deadline is not None and round_deadline <= attempt_deadline)
    if not _HTTP_ATTEMPT_POOL.submit_until(
            _run, effective_deadline, cancel_event=round_cancel):
        local_cancel.set()
        if deadline_is_round or (round_cancel is not None and round_cancel.is_set()):
            raise _RefreshDeadlineError("refresh deadline reached")
        raise _AttemptDeadlineError(
            f"attempt exceeded {float(timeout_s):g}s wall-clock deadline")
    while True:
        if round_cancel is not None and round_cancel.is_set():
            local_cancel.set()
            _close_inflight_response(state)
            raise _RefreshDeadlineError("refresh deadline reached")
        remaining = effective_deadline - time.monotonic()
        if remaining <= 0:
            local_cancel.set()
            _close_inflight_response(state)
            if deadline_is_round:
                raise _RefreshDeadlineError("refresh deadline reached")
            raise _AttemptDeadlineError(
                f"attempt exceeded {float(timeout_s):g}s wall-clock deadline")
        if not done.wait(min(remaining, 0.05)):
            continue
        # A result that arrived only after the absolute boundary is still timed out.
        if time.monotonic() > effective_deadline:
            local_cancel.set()
            _close_inflight_response(state)
            if deadline_is_round:
                raise _RefreshDeadlineError("refresh deadline reached")
            raise _AttemptDeadlineError(
                f"attempt exceeded {float(timeout_s):g}s wall-clock deadline")
        if "error" in result_box:
            raise result_box["error"]
        if "value" not in result_box:
            raise RuntimeError("HTTP attempt ended without a result")
        return result_box["value"]


def _fetch_source(src: Dict[str, Any], opener=None, sleep_fn=None,
                  max_attempts: Optional[int] = None,
                  timeout_s: Optional[float] = None, *,
                  cancel_event: Optional[threading.Event] = None,
                  deadline_monotonic: Optional[float] = None) -> Dict[str, Any]:
    """抓单源；每次尝试和整轮均有严格墙钟边界，迟到结果不会进入缓存。"""
    import urllib.error
    import urllib.request

    url = str(src.get("url") or "").strip()
    started = time.monotonic()
    attempts_cap = max(1, int(max_attempts if max_attempts is not None else _MAX_ATTEMPTS))
    timeout = max(0.01, float(timeout_s if timeout_s is not None else _FETCH_TIMEOUT))
    base = {"source": src.get("name") or "", "sector": src.get("hint") or "",
            "url": url, "http_status": None, "content_type": "", "items": []}
    if not url:
        return {**base, "status": "error", "error": "ConfigError: missing url",
                "error_kind": "missing_url", "retryable": False, "attempts": 0,
                "latency_ms": 0, "items_parsed": 0, "freshest_source_time": ""}

    _open = opener or urllib.request.urlopen
    last_kind = "unknown"
    last_status: Optional[int] = None
    last_content_type = ""
    last_retryable = False
    last_error = "unknown error"
    for attempt in range(1, attempts_cap + 1):
        attempt_state: Dict[str, Any] = {}

        def _attempt(local_cancel: threading.Event,
                     inflight: Dict[str, Any]) -> Dict[str, Any]:
            if local_cancel.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise _RefreshDeadlineError("refresh deadline reached")
            req = urllib.request.Request(url, headers={
                # 保留主线对部分公开 RSS 兼容更好的浏览器 UA；不换身份绕 WAF。
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
                "Connection": "close",
            })
            # 不传自定义 SSL context：证书和主机名验证保持系统默认严格策略。
            try:
                response = _open(req, timeout=timeout)  # noqa: S310 — 固定 vendored 白名单源
            except urllib.error.HTTPError as exc:
                # HTTPError 同时也是响应对象。429/5xx 是否可重试，必须先在本次
                # strict-deadline worker 内审计 Content-Type 和有限响应体；HTML/WAF
                # 永不重试。读取若卡住，仍由本次 attempt deadline 截断。
                inflight["response"] = exc
                content_type = _response_content_type(exc)
                attempt_state["http_status"] = int(exc.code)
                attempt_state["content_type"] = content_type
                # Content-Type 已明确为 HTML 时无需再冒险读取可能永久阻塞的正文。
                no_retry = _is_html_document(b"", content_type)
                error_body = b""
                if not no_retry:
                    try:
                        error_body = exc.read(8192)
                    except Exception:  # noqa: BLE001 - 错误正文仅用于重试安全判定
                        error_body = b""
                    no_retry = _is_waf_or_challenge_response(error_body, content_type)
                setattr(exc, "_newsradar_no_retry", no_retry)
                setattr(exc, "_newsradar_content_type", content_type)
                _close_inflight_response(inflight)
                raise
            inflight["response"] = response
            if local_cancel.is_set() or (cancel_event is not None and cancel_event.is_set()):
                _close_inflight_response(inflight)
                raise _RefreshDeadlineError("refresh deadline reached")
            with response:
                attempt_state["http_status"] = int(
                    getattr(response, "status", None)
                    or getattr(response, "code", None) or 200)
                attempt_state["content_type"] = _response_content_type(response)
                raw = _read_feed_response_bounded(
                    response, attempt_state["content_type"],
                )
            if local_cancel.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise _RefreshDeadlineError("refresh deadline reached")
            try:
                adapter = str(src.get("adapter") or "").strip()
                if not adapter:
                    # 原始 bytes 交给 XML 解析器，让 XML 声明决定编码。
                    parsed_items = _parse_feed_checked(
                        raw, base["source"], base["sector"])
                elif adapter == "wallstreetcn_information_flow_v1":
                    parsed_items = _parse_wallstreetcn_information_flow(
                        raw, base["source"], base["sector"])
                else:
                    raise _InvalidFeedError(
                        f"unsupported source adapter: {adapter}", retryable=False,
                    )
            except ET.ParseError as exc:
                html = _is_html_document(raw, attempt_state["content_type"])
                kind = "HTML/challenge response" if html else "invalid XML"
                raise _InvalidFeedError(
                    f"{kind}: {str(exc)[:120]}", retryable=not html) from exc
            freshest = max((int(item.get("ts") or 0) for item in parsed_items), default=0)
            items = parsed_items[:_MAX_ITEMS_PER_SOURCE]
            return {
                "http_status": attempt_state["http_status"],
                "content_type": attempt_state["content_type"],
                "items": items, "items_seen": len(parsed_items),
                "freshest_source_time": _iso_utc(freshest),
            }

        try:
            attempt_result = _strict_wall_clock_call(
                _attempt, timeout_s=timeout, round_cancel=cancel_event,
                round_deadline=deadline_monotonic)
            items = attempt_result["items"]
            return {**base, "http_status": attempt_result["http_status"],
                    "content_type": attempt_result["content_type"],
                    "status": "ok" if items else "empty", "error": "", "items": items,
                    "error_kind": "", "retryable": False, "attempts": attempt,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "items_parsed": len(items), "items_seen": attempt_result["items_seen"],
                    "items_truncated": max(0, attempt_result["items_seen"] - len(items)),
                    "freshest_source_time": attempt_result["freshest_source_time"]}
        except Exception as exc:  # noqa: BLE001 — 转成逐源可审计失败，不拖垮整轮
            if attempt_state.get("http_status") is not None:
                last_status = int(attempt_state["http_status"])
            if attempt_state.get("content_type") is not None:
                last_content_type = str(attempt_state["content_type"])
            last_kind, classified_status, last_retryable = _classify_error(exc)
            if isinstance(exc, _InvalidFeedError) and "text/html" in last_content_type.lower():
                last_retryable = False
            if classified_status is not None:
                last_status = classified_status
            if isinstance(exc, urllib.error.HTTPError):
                last_content_type = str(
                    getattr(exc, "_newsradar_content_type", "")
                    or (exc.headers or {}).get("Content-Type") or "")[:120]
                if bool(getattr(exc, "_newsradar_no_retry", False)):
                    last_retryable = False
                last_error = f"HTTPError: HTTP {exc.code} {exc.reason}"
                if not last_retryable and _is_html_document(b"", last_content_type):
                    last_error += " (HTML/challenge response; retry suppressed)"
            elif isinstance(exc, _InvalidFeedError):
                last_error = f"FeedFormatError: {str(exc)[:160]}"
            else:
                last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            if not last_retryable or attempt >= attempts_cap:
                return {**base, "http_status": last_status,
                        "content_type": last_content_type,
                        "status": "error", "error": last_error,
                        "error_kind": last_kind, "retryable": last_retryable,
                        "attempts": attempt,
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "items_parsed": 0, "items_seen": 0, "items_truncated": 0,
                        "freshest_source_time": ""}
            delay = min(_BACKOFF_MAX_S, _BACKOFF_BASE_S * (2 ** (attempt - 1)))
            if delay <= 0:
                continue
            if sleep_fn is not None:
                sleep_fn(delay)
            else:
                remaining = (deadline_monotonic - time.monotonic()
                             if deadline_monotonic is not None else delay)
                wait_s = min(delay, max(0.0, remaining))
                if cancel_event is not None:
                    if cancel_event.wait(wait_s):
                        return {**base, "status": "error",
                                "error": "_RefreshDeadlineError: refresh deadline reached",
                                "error_kind": "refresh_deadline", "retryable": False,
                                "attempts": attempt,
                                "latency_ms": round((time.monotonic() - started) * 1000),
                                "items_parsed": 0, "items_seen": 0,
                                "items_truncated": 0, "freshest_source_time": ""}
                else:
                    time.sleep(wait_s)
                if wait_s < delay or (deadline_monotonic is not None
                                      and time.monotonic() >= deadline_monotonic):
                    return {**base, "status": "error",
                            "error": "_RefreshDeadlineError: refresh deadline reached",
                            "error_kind": "refresh_deadline", "retryable": False,
                            "attempts": attempt,
                            "latency_ms": round((time.monotonic() - started) * 1000),
                            "items_parsed": 0, "items_seen": 0,
                            "items_truncated": 0, "freshest_source_time": ""}

    return {**base, "http_status": last_status, "content_type": last_content_type,
            "status": "error", "error": last_error, "error_kind": last_kind,
            "retryable": last_retryable, "attempts": attempts_cap,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "items_parsed": 0, "items_seen": 0, "items_truncated": 0,
            "freshest_source_time": ""}


def fetch_feed(src: Dict[str, Any], opener=None, sleep_fn=None,
               max_attempts: Optional[int] = None,
               timeout_s: Optional[float] = None) -> List[Dict[str, Any]]:
    """兼容旧调用：抓单源仅返回 items；完整逐源状态由聚合器内部保存。"""
    return list(_fetch_source(
        src, opener=opener, sleep_fn=sleep_fn,
        max_attempts=max_attempts, timeout_s=timeout_s,
    ).get("items") or [])


def _read_cache() -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


@contextmanager
def _cache_write_lock(timeout_s: float = _CACHE_LOCK_TIMEOUT_S):
    """跨进程独占锁；进程异常退出时由 OS 自动释放，锁文件本身可长期复用。"""
    lock_path = _CACHE_PATH.with_name(f"{_CACHE_PATH.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    acquired = False
    is_windows = os.name == "nt"
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.01, float(timeout_s))
        if is_windows:
            import msvcrt
            while not acquired:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("newsradar cache lock timed out")
                    time.sleep(0.01)
        else:
            import fcntl
            while not acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("newsradar cache lock timed out")
                    time.sleep(0.01)
        yield
    finally:
        if acquired:
            try:
                if is_windows:
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _snapshot_quality(data: Dict[str, Any]) -> tuple[int, int, int, int, int]:
    """并发刷新择优：先看本轮成功源，再看可用覆盖与有内容源/条目。"""
    health = data.get("source_health") if isinstance(data.get("source_health"), dict) else {}
    counts = health.get("counts") if isinstance(health.get("counts"), dict) else {}

    def _integer(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    fresh = _integer(counts.get("fresh") if counts else data.get("sources_ok"))
    last_good = _integer(counts.get("last-good") if counts else 0)
    hard_failure = _integer(
        counts.get("hard-failure") if counts else data.get("sources_failed"))
    items = data.get("items") if isinstance(data.get("items"), list) else []
    return (
        _integer(data.get("sources_ok")) or fresh,
        fresh + last_good,
        _integer(data.get("sources_with_items")),
        len(items),
        -hard_failure,
    )


def _cache_candidate_wins(existing: Optional[Dict[str, Any]],
                          candidate: Dict[str, Any]) -> bool:
    """顺序新轮照常替换；墙钟区间重叠的多进程轮次只允许更完整快照胜出。"""
    if not existing:
        return True
    try:
        old_start = int(existing.get("refresh_started_ns") or 0)
        old_end = int(existing.get("refresh_completed_ns") or 0)
        new_start = int(candidate.get("refresh_started_ns") or 0)
        new_end = int(candidate.get("refresh_completed_ns") or 0)
    except (TypeError, ValueError, OverflowError):
        return True
    # 旧 schema 没有刷新区间时保持向后兼容，由当前完整轮升级。
    if min(old_start, old_end, new_start, new_end) <= 0:
        return True
    if new_start > old_end:       # 明确开始于旧轮完成之后：这是下一代快照
        return True
    if new_end < old_start:       # 明确早于现存轮完成：迟到的旧代不得倒灌
        return False
    old_quality = _snapshot_quality(existing)
    new_quality = _snapshot_quality(candidate)
    if new_quality != old_quality:
        return new_quality > old_quality
    # 同质量的重叠轮次用更晚完成/开始者稳定收敛。
    return (new_end, new_start) >= (old_end, old_start)


def _write_cache(data: Dict[str, Any]) -> bool:
    tmp: Optional[Path] = None
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _cache_write_lock():
            existing = _read_cache()
            if not _cache_candidate_wins(existing, data):
                return False
            tmp = _CACHE_PATH.with_name(
                f"{_CACHE_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, _CACHE_PATH)
            tmp = None
        return True
    except OSError:
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _cache_fresh(cached: Optional[Dict[str, Any]], ttl_s: int) -> bool:
    if not cached:
        return False
    return (int(datetime.now().timestamp()) - int(cached.get("pulled_ts") or 0)) < int(ttl_s)


def _source_key(row: Dict[str, Any]) -> str:
    return (f"{row.get('sector') or row.get('hint') or ''}\x1f"
            f"{row.get('source') or row.get('name') or ''}\x1f"
            f"{row.get('source_url') or row.get('url') or ''}")


def _deadline_source_result(source: Dict[str, Any], elapsed_ms: int) -> Dict[str, Any]:
    return {
        "source": source.get("name") or "", "sector": source.get("hint") or "",
        "url": source.get("url") or "", "http_status": None,
        "content_type": "", "items": [], "status": "error",
        "error": "_RefreshDeadlineError: refresh deadline reached",
        "error_kind": "refresh_deadline", "retryable": False, "attempts": 0,
        "latency_ms": max(0, int(elapsed_ms)), "items_parsed": 0,
        "items_seen": 0, "items_truncated": 0, "freshest_source_time": "",
    }


def _scheduled_fetch(source: Dict[str, Any], cancel_event: threading.Event,
                     deadline_monotonic: float) -> Dict[str, Any]:
    """保留旧测试/注入的一参数 fetcher，同时给生产 fetcher 传取消上下文。"""
    import inspect

    try:
        parameters = inspect.signature(_fetch_source).parameters.values()
        supports_context = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            or parameter.name == "cancel_event" for parameter in parameters)
    except (TypeError, ValueError):
        supports_context = True
    if supports_context:
        return _fetch_source(
            source, cancel_event=cancel_event,
            deadline_monotonic=deadline_monotonic)
    return _fetch_source(source)


def _fetch_sources_scheduled(sources: List[Dict[str, Any]], *,
                             deadline_s: Optional[float] = None) -> List[Dict[str, Any]]:
    """按主机限流，并在整轮绝对 deadline 后取消/拒收所有迟到结果。"""
    if not sources:
        return []
    started = time.monotonic()
    round_deadline = started + max(
        0.01, float(deadline_s if deadline_s is not None else _REFRESH_DEADLINE_S))
    cancel_event = threading.Event()
    worker_cap = min(_MAX_WORKERS, len(sources))
    pending = sorted(
        enumerate(sources), key=lambda pair: (_priority_rank(pair[1]), pair[0]))
    active_by_host: Dict[str, int] = {}
    by_index: Dict[int, Dict[str, Any]] = {}
    active: Dict[Any, tuple[int, Dict[str, Any], str]] = {}
    deadline_reached = False
    executor = ThreadPoolExecutor(max_workers=worker_cap,
                                  thread_name_prefix="newsradar")
    try:
        while pending or active:
            if time.monotonic() >= round_deadline:
                deadline_reached = True
                break
            while pending and len(active) < worker_cap:
                eligible = next((position for position, (_index, source) in enumerate(pending)
                                 if active_by_host.get(_source_host(source), 0)
                                 < _PER_HOST_CONCURRENCY), None)
                if eligible is None:
                    break
                index, source = pending.pop(eligible)
                host = _source_host(source)
                active_by_host[host] = active_by_host.get(host, 0) + 1
                active[executor.submit(
                    _scheduled_fetch, source, cancel_event, round_deadline
                )] = (index, source, host)
            if not active:  # _PER_HOST_CONCURRENCY >= 1，理论不可达
                break
            remaining = round_deadline - time.monotonic()
            if remaining <= 0:
                deadline_reached = True
                break
            completed, _ = wait(
                tuple(active), timeout=remaining, return_when=FIRST_COMPLETED)
            if not completed:
                deadline_reached = True
                break
            for future in completed:
                index, source, host = active.pop(future)
                active_by_host[host] = max(0, active_by_host.get(host, 1) - 1)
                try:
                    by_index[index] = future.result()
                except Exception as exc:  # noqa: BLE001 - 注入 fetcher 也不能拖垮整轮
                    kind, status, retryable = _classify_error(exc)
                    by_index[index] = {
                        "source": source.get("name") or "",
                        "sector": source.get("hint") or "",
                        "url": source.get("url") or "",
                        "http_status": status, "content_type": "", "items": [],
                        "status": "error", "error": f"{type(exc).__name__}: {exc}",
                        "error_kind": kind, "retryable": retryable, "attempts": 1,
                        "latency_ms": 0, "items_parsed": 0, "items_seen": 0,
                        "items_truncated": 0, "freshest_source_time": "",
                    }
    finally:
        if deadline_reached:
            cancel_event.set()
            for future in active:
                future.cancel()
        # deadline 路径不等待第三方/测试 fetcher；生产 fetcher 会观察 cancel_event
        # 并关闭响应。无论何种路径，future 都没有任何缓存写能力。
        executor.shutdown(wait=not deadline_reached, cancel_futures=True)
    if deadline_reached:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        for index, source in enumerate(sources):
            by_index.setdefault(index, _deadline_source_result(source, elapsed_ms))
    return [by_index[index] for index in range(len(sources)) if index in by_index]


def _previous_success_ts(previous: Dict[str, Any], cached: Optional[Dict[str, Any]],
                         *, allow_legacy_pull: bool = False) -> int:
    direct = int(previous.get("last_success_ts") or previous.get("last_good_ts") or 0)
    if direct > 0:
        return direct
    parsed = _parse_iso_ts(previous.get("last_success_at") or previous.get("last_good_at"))
    if parsed > 0:
        return parsed
    if not allow_legacy_pull:
        return 0
    # schema-v1/v2 迁移：仅当该源确实匹配到旧条目时，整包 pulled_ts 才能作为
    # 旧快照真实成功时刻；新加入的源不能借用别人的成功时间冒充 last-good。
    try:
        return max(0, int((cached or {}).get("pulled_ts") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _freshness_status(row: Dict[str, Any], now_ts: int,
                      last_good_max_age_s: int) -> tuple[str, int, Optional[int], bool]:
    """返回三态、last-good 时间、年龄和是否过期；保留旧 ``status`` 契约。"""
    fetch_status = str(row.get("status") or "")
    success_ts = int(row.get("last_success_ts") or row.get("last_good_ts") or 0)
    if success_ts <= 0:
        success_ts = _parse_iso_ts(row.get("last_success_at") or row.get("last_good_at"))
    age = now_ts - success_ts if success_ts > 0 else None
    if fetch_status in ("ok", "empty"):
        return "fresh", success_ts, max(0, age) if age is not None else None, False
    usable = success_ts > 0 and age is not None and 0 <= age <= last_good_max_age_s
    if usable:
        return "last-good", success_ts, age, False
    expired = bool(success_ts > 0 and (age is None or age < 0 or age > last_good_max_age_s))
    return "hard-failure", success_ts, max(0, age) if age is not None else None, expired


def _source_health(statuses: List[Dict[str, Any]], now_ts: int,
                   last_good_max_age_s: int) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    counts = {"fresh": 0, "last-good": 0, "hard-failure": 0}
    for raw in statuses:
        source = dict(raw)
        state, success_ts, age, expired = _freshness_status(
            source, now_ts, last_good_max_age_s)
        source["fetch_status"] = source.get("status")
        source["status"] = state
        source["last_good_ts"] = success_ts or None
        source["last_good_age_s"] = age
        source["last_good_expired"] = expired
        counts[state] += 1
        rows.append(source)
    total = len(rows)
    available = counts["fresh"] + counts["last-good"]
    if total == 0:
        state = "empty"
    elif counts["fresh"] == total:
        state = "fresh"
    elif available:
        state = "degraded"
    else:
        state = "unavailable"
    return {
        "total": total,
        "fresh": counts["fresh"],
        "last-good": counts["last-good"],
        "hard-failure": counts["hard-failure"],
        "last_good": counts["last-good"],
        "hard_failure": counts["hard-failure"],
        "counts": counts,
        "fresh_rate": round(counts["fresh"] / total, 4) if total else 0.0,
        "available_rate": round(available / total, 4) if total else 0.0,
        "state": state,
        "sources": rows,
    }


def read_radar(sectors: Optional[List[str]] = None, days: int = 7, limit: int = 200,
               use_cache: bool = True, ttl_s: int = _DEFAULT_TTL_S,
               include_future: bool = False,
               last_good_max_age_s: Optional[int] = None) -> Dict[str, Any]:
    """聚合:选中赛道(None=全部)的一手 RSS,近 `days` 天、按时间倒序、上限 `limit`。
    并发抓取(线程池);use_cache 时先读 TTL 内磁盘缓存(避免每次打全部配置源)。纯情报,不进信号。"""
    max_age = max(0, int(last_good_max_age_s if last_good_max_age_s is not None
                         else _LAST_GOOD_MAX_AGE_S))
    cached = _read_cache() if use_cache else None
    if use_cache and _cache_fresh(cached, ttl_s):
        return _slice(cached or {}, sectors, days, limit, include_future=include_future,
                      last_good_max_age_s=max_age)

    # 同进程 refresh 单飞；events 同步入库与 SWR 后台若同时发现过期，后到者等待
    # 首次刷新并二次读缓存，不再重复打 106 源或争写同一快照。
    with _LOCK:
        cached = _read_cache() if use_cache else None
        if use_cache and _cache_fresh(cached, ttl_s):
            return _slice(cached or {}, sectors, days, limit, include_future=include_future,
                          last_good_max_age_s=max_age)
        return _refresh_radar(
            sectors=sectors, days=days, limit=limit, use_cache=use_cache,
            include_future=include_future, cached=cached,
            last_good_max_age_s=max_age,
        )


def _refresh_radar(*, sectors: Optional[List[str]], days: int, limit: int,
                   use_cache: bool, include_future: bool,
                   cached: Optional[Dict[str, Any]],
                   last_good_max_age_s: int) -> Dict[str, Any]:

    industries, all_sources = load_sources()
    want = set(sectors) if sectors else None
    picked = [s for s in all_sources if (want is None or s.get("hint") in want)]
    refresh_started_ns = time.time_ns()
    refresh_started = time.monotonic()
    results = _fetch_sources_scheduled(picked)
    refresh_elapsed_ms = round((time.monotonic() - refresh_started) * 1000)
    refresh_completed_ns = time.time_ns()

    pulled_ts = int(datetime.now().timestamp())
    pulled_at = datetime.now().isoformat(timespec="seconds")
    attempted_at = _iso_utc(pulled_ts)
    old_status = {
        _source_key(row): row for row in ((cached or {}).get("source_status") or [])
        if isinstance(row, dict)
    }
    configured_urls: Dict[tuple[str, str], set[str]] = {}
    for source in picked:
        origin = (str(source.get("hint") or ""), str(source.get("name") or ""))
        if source.get("url"):
            configured_urls.setdefault(origin, set()).add(str(source["url"]))
    old_items: Dict[str, List[Dict[str, Any]]] = {}
    for item in ((cached or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        # 与 source_status 使用同一身份 `(sector, source, feed_url)`；旧缓存若没有
        # source_url，仅当配置表对 `(sector,source)` 唯一时才可确定性补齐；否则不猜。
        migrated = dict(item)
        if not migrated.get("source_url"):
            urls = configured_urls.get(
                (str(migrated.get("sector") or ""), str(migrated.get("source") or "")),
                set(),
            )
            if len(urls) == 1:
                migrated["source_url"] = next(iter(urls))
                migrated["source_url_inferred_from_catalog"] = True
        if migrated.get("source_url"):
            old_items.setdefault(_source_key(migrated), []).append(migrated)

    items: List[Dict[str, Any]] = []
    source_status: List[Dict[str, Any]] = []
    for result in results:
        result_key = _source_key(result)
        previous = old_status.get(result_key, {})
        ok = result.get("status") in ("ok", "empty")
        previous_success_ts = _previous_success_ts(
            previous, cached, allow_legacy_pull=bool(old_items.get(result_key)))
        previous_age = pulled_ts - previous_success_ts if previous_success_ts > 0 else None
        usable_previous = bool(
            not ok and previous_success_ts > 0 and previous_age is not None
            and 0 <= previous_age <= last_good_max_age_s)
        freshness_status = "fresh" if ok else "last-good" if usable_previous else "hard-failure"
        success_ts = pulled_ts if ok else previous_success_ts
        success_at = (attempted_at if ok else previous.get("last_success_at")
                      or _iso_utc(previous_success_ts))
        fresh_items = [dict(item, source_url=result.get("url") or "",
                            retrieved_at=attempted_at, stale_source=False,
                            source_status="fresh", source_fetched_ts=pulled_ts)
                       for item in (result.get("items") or []) if isinstance(item, dict)]
        carried: List[Dict[str, Any]] = []
        if usable_previous:
            carried = [dict(item, stale_source=True,
                            source_last_success_at=success_at,
                            source_status="last-good",
                            source_fetched_ts=previous_success_ts)
                       for item in old_items.get(result_key, [])]
        items.extend(fresh_items if ok else carried)
        source_status.append({
            "source": result.get("source"), "sector": result.get("sector"),
            "url": result.get("url"), "status": result.get("status"),
            "http_status": result.get("http_status"),
            "content_type": result.get("content_type") or "",
            "latency_ms": result.get("latency_ms"),
            "items_parsed": result.get("items_parsed", 0),
            "items_seen": result.get("items_seen", result.get("items_parsed", 0)),
            "items_truncated": result.get("items_truncated", 0),
            "attempts": result.get("attempts", 1),
            "error_kind": result.get("error_kind") or "",
            "retryable": bool(result.get("retryable")),
            "freshest_source_time": (result.get("freshest_source_time") or
                                      previous.get("freshest_source_time") or ""),
            "last_success_at": success_at or None,
            "last_success_ts": success_ts or None,
            "freshness_status": freshness_status,
            "last_good_age_s": (0 if ok else max(0, previous_age)
                                if previous_age is not None else None),
            "last_good_expired": bool(
                not ok and previous_success_ts > 0 and not usable_previous),
            "last_error": "" if ok else result.get("error") or "unknown error",
            "consecutive_failures": 0 if ok else int(previous.get("consecutive_failures") or 0) + 1,
            "served_from_last_good": bool(carried),
            "stale_items_carried": len(carried),
            "items_undated": sum(
                1 for item in (fresh_items if ok else carried)
                if int(item.get("ts") or 0) <= 0
            ),
        })
    failures = [
        {"source": row.get("source"), "sector": row.get("sector"),
         "error": row.get("last_error")}
        for row in source_status if row.get("status") == "error"
    ]
    sources_ok = sum(1 for row in source_status if row.get("status") in ("ok", "empty"))
    sources_with_items = sum(1 for row in source_status if row.get("status") == "ok")
    sources_empty = sum(1 for row in source_status if row.get("status") == "empty")
    all_failed = bool(results) and sources_ok == 0
    cached_pulled_ts = int((cached or {}).get("pulled_ts") or 0)
    cached_age = pulled_ts - cached_pulled_ts if cached_pulled_ts > 0 else None
    legacy_last_good_usable = bool(
        cached and cached.get("items") and cached_age is not None
        and 0 <= cached_age <= last_good_max_age_s)
    if all_failed and not items and legacy_last_good_usable:
        # schema v1 没有 source_url，首次升级又恰逢全网失败时仍必须保住整份最后好快照；
        # 在 last-good 时限内保住整份快照；过期后绝不继续配送。
        items = [dict(item, stale_source=True, source_status="last-good",
                      source_fetched_ts=cached_pulled_ts,
                      source_last_success_at=_iso_utc(cached_pulled_ts))
                 for item in ((cached or {}).get("items") or []) if isinstance(item, dict)]
    data_pulled_ts = int((cached or {}).get("pulled_ts") or 0) if all_failed and cached else pulled_ts
    data_pulled_at = ((cached or {}).get("pulled_at") if all_failed and cached else pulled_at)
    data_retrieved_at = ((cached or {}).get("retrieved_at") if all_failed and cached else attempted_at)
    data = {
        "cache_schema": _CACHE_SCHEMA_VERSION,
        "pulled_ts": data_pulled_ts,
        "pulled_at": data_pulled_at,
        "retrieved_at": data_retrieved_at,
        "refresh_attempted_ts": pulled_ts,
        "refresh_attempted_at": attempted_at,
        "refresh_started_ns": refresh_started_ns,
        "refresh_completed_ns": refresh_completed_ns,
        "refresh_deadline_s": _REFRESH_DEADLINE_S,
        "refresh_elapsed_ms": refresh_elapsed_ms,
        "refresh_deadline_exceeded": any(
            row.get("error_kind") == "refresh_deadline" for row in results),
        "last_good_max_age_s": int(last_good_max_age_s),
        "n_sources": len(picked),                 # 旧字段兼容 = 本轮尝试源数
        "sources_tried": len(picked),
        "sources_ok": sources_ok,                # HTTP+XML 成功；合法空源也计成功
        "sources_with_items": sources_with_items,
        "sources_empty": sources_empty,
        "sources_failed": len(failures),
        "source_status": source_status,
        "source_health": _source_health(
            source_status, pulled_ts, last_good_max_age_s),
        "failures": failures,
        "last_good_cache_preserved": bool(all_failed and cached and items),
        "stale_items_carried": len(items) if all_failed and cached and items else sum(
            int(row.get("stale_items_carried") or 0) for row in source_status
        ),
        "undated_count": sum(
            1 for item in items if int(item.get("ts") or 0) <= 0
        ),
        "industries": [{"key": i.get("key"), "name": i.get("name")}
                       for i in industries if isinstance(i, dict) and i.get("key")],
        "items": items,
    }
    if use_cache and want is None:                # 只缓存全量抓取,子集不污染缓存
        _write_cache(data)
    return _slice(data, sectors, days, limit, include_future=include_future,
                  last_good_max_age_s=last_good_max_age_s)


def _trigger_radar() -> bool:
    """单飞:后台全量抓配置源并写缓存(供 SWR 非阻塞路由用)。已在跑 → False。"""
    with _WARM_LOCK:
        if _WARM_INFLIGHT[0]:
            return False
        _WARM_INFLIGHT[0] = True

    def _run() -> None:
        try:
            read_radar(sectors=None, days=30, use_cache=True, ttl_s=0)   # ttl_s=0 强制现抓+写缓存
        except Exception:  # noqa: BLE001
            pass
        finally:
            _WARM_INFLIGHT[0] = False

    try:
        threading.Thread(target=_run, daemon=True).start()
    except RuntimeError:
        _WARM_INFLIGHT[0] = False
        return False
    return True


def _radar_refreshing() -> bool:
    with _WARM_LOCK:
        return bool(_WARM_INFLIGHT[0])


def _refresh_due(cached: Optional[Dict[str, Any]], retry_s: int = _REFRESH_RETRY_S) -> bool:
    if not cached:
        return True
    now_ts = int(datetime.now().timestamp())
    last_attempt = int(cached.get("refresh_attempted_ts") or cached.get("pulled_ts") or 0)
    return last_attempt <= 0 or now_ts - last_attempt >= max(1, int(retry_s))


def read_radar_swr(sectors: Optional[List[str]] = None, days: int = 7, limit: int = 200,
                   ttl_s: int = _DEFAULT_TTL_S, include_future: bool = False,
                   force_refresh: bool = False,
                   last_good_max_age_s: Optional[int] = None,
                   allow_refresh: bool = True) -> Dict[str, Any]:
    """非阻塞 SWR(web 路由用):有 TTL 内磁盘缓存 → 秒回切片;过期/无缓存 → 触发后台全量抓,
    无缓存时返回 warming(绝不在请求线程里阻塞扫描全部配置源)。"""
    max_age = max(0, int(last_good_max_age_s if last_good_max_age_s is not None
                         else _LAST_GOOD_MAX_AGE_S))
    cached = _read_cache()
    age_fresh = _cache_fresh(cached, ttl_s)
    schema_current = bool(cached) and int((cached or {}).get("cache_schema") or 0) >= _CACHE_SCHEMA_VERSION
    needs_schema_upgrade = bool(cached) and not schema_current
    refresh_started = False
    refresh_due = _refresh_due(cached)
    if allow_refresh and (
            force_refresh or not cached or needs_schema_upgrade
            or (schema_current and not age_fresh and refresh_due)):
        refresh_started = _trigger_radar()
    if not cached:
        try:
            configured = len(load_sources()[1])
        except Exception:  # noqa: BLE001
            configured = None
        return {"warming": bool(allow_refresh), "refreshing": refresh_started,
                "stale": not allow_refresh, "cache_only": not allow_refresh, "items": [],
                "future_items": [], "n": 0, "sources_tried": 0,
                "configured_sources": configured,
                "source_health": _source_health([], int(datetime.now().timestamp()), max_age),
                "last_good_max_age_s": max_age,
                "source_quality": "unknown", "quality": "unknown",
                "note": (
                    "资讯雷达预热中(后台首次扫描配置源已触发),稍后重试。"
                    if allow_refresh else
                    "NewsRadar 只读缓存为空；Codex 自动通道未触发共享后台刷新。"
                )}
    out = _slice(cached, sectors, days, limit, include_future=include_future,
                 last_good_max_age_s=max_age)
    out["warming"] = False
    out["stale"] = not age_fresh
    refreshing = bool(refresh_started or _radar_refreshing())
    out["refreshing"] = refreshing
    out["refresh_backoff"] = bool(schema_current and not age_fresh
                                  and not refresh_due and not refreshing)
    if out["refresh_backoff"]:
        last_attempt = int((cached or {}).get("refresh_attempted_ts") or 0)
        elapsed = max(0, int(datetime.now().timestamp()) - last_attempt)
        out["refresh_retry_after_s"] = max(0, _REFRESH_RETRY_S - elapsed)
    out["cache_schema_current"] = schema_current
    out["cache_only"] = not allow_refresh
    return out


def _decorated_item(item: Dict[str, Any], bucket: str) -> Dict[str, Any]:
    row = dict(item)
    ts = int(row.get("ts") or 0)
    row.setdefault("source_time", _iso_utc(ts))
    row.setdefault("source_time_raw", "")
    # 旧缓存只有 ts，无法反推它究竟来自 published/updated/date，不能补写成 published。
    row.setdefault("time_basis", "unknown")
    row.setdefault("published_at", row.get("source_time") if row.get("time_basis") == "published" else "")
    row.setdefault("updated_at", row.get("source_time") if row.get("time_basis") == "updated" else "")
    if ts <= 0:
        row["time_status"] = "undated"
        row["is_undated"] = True
        row.setdefault(
            "undated_reason",
            "source_time_unparseable_or_timezone_missing"
            if row.get("source_time_raw") else "source_omitted_time",
        )
        # Retrieval time is deliberately not promoted to publication time.
        row.setdefault("time_precision", "unknown")
        row.setdefault("time_source", "publisher_feed_missing_or_unverified")
        row.setdefault("time_confidence", 0.0)
    else:
        row.setdefault("time_status", "verified_source_time")
        row.setdefault("is_undated", False)
        row.setdefault("undated_reason", "")
    # Vibe-Research rss_news 的兼容别名；url/sector/ts 继续是观澜既有契约。
    row.setdefault("link", row.get("url") or "")
    row.setdefault("industry", row.get("sector") or "")
    row.setdefault(
        "published",
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        if ts > 0 and row.get("time_basis") == "published" else "",
    )
    row["time_bucket"] = bucket
    return row


def _slice(data: Dict[str, Any], sectors, days: int, limit: int,
           *, include_future: bool = False,
           last_good_max_age_s: Optional[int] = None) -> Dict[str, Any]:
    now_ts = int(datetime.now().timestamp())
    days = max(1, min(int(days), 30))
    limit = max(1, min(int(limit), 500))
    cutoff = now_ts - days * 86400
    want = set(sectors) if sectors else None
    max_age = max(0, int(last_good_max_age_s if last_good_max_age_s is not None
                         else data.get("last_good_max_age_s", _LAST_GOOD_MAX_AGE_S)))

    statuses: List[Dict[str, Any]] = []
    status_by_key: Dict[str, Dict[str, Any]] = {}
    status_by_origin: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for raw in (data.get("source_status") or []):
        if not isinstance(raw, dict) or (want is not None and raw.get("sector") not in want):
            continue
        row = dict(raw)
        state, success_ts, age, expired = _freshness_status(row, now_ts, max_age)
        row["freshness_status"] = state
        row["last_success_ts"] = success_ts or None
        row["last_good_age_s"] = age
        row["last_good_expired"] = expired
        if state == "hard-failure" and row.get("served_from_last_good"):
            row["cached_stale_items_carried"] = int(row.get("stale_items_carried") or 0)
            row["served_from_last_good"] = False
            row["stale_items_carried"] = 0
        statuses.append(row)
        status_by_key[_source_key(row)] = row
        origin = (str(row.get("sector") or ""), str(row.get("source") or ""))
        status_by_origin.setdefault(origin, []).append(row)

    candidates: List[Dict[str, Any]] = []
    for raw in (data.get("items") or []):
        if not isinstance(raw, dict) or (want is not None and raw.get("sector") not in want):
            continue
        item = dict(raw)
        is_last_good = bool(item.get("stale_source") or item.get("source_status") == "last-good")
        if is_last_good:
            source_row = status_by_key.get(_source_key(item))
            if source_row is None:
                origin = (str(item.get("sector") or ""), str(item.get("source") or ""))
                matches = status_by_origin.get(origin) or []
                source_row = matches[0] if len(matches) == 1 else None
            fetched_ts = int(item.get("source_fetched_ts") or 0)
            if fetched_ts <= 0:
                fetched_ts = _parse_iso_ts(item.get("source_last_success_at"))
            if fetched_ts <= 0 and source_row is not None:
                fetched_ts = int(source_row.get("last_success_ts") or 0)
            if fetched_ts <= 0:
                fetched_ts = int(data.get("pulled_ts") or 0)
            age = now_ts - fetched_ts if fetched_ts > 0 else None
            if (source_row is not None
                    and source_row.get("freshness_status") == "hard-failure"):
                continue
            if fetched_ts <= 0 or age is None or not (0 <= age <= max_age):
                continue
            item["source_status"] = "last-good"
            item["source_fetched_ts"] = fetched_ts
        candidates.append(item)
    recent = [_decorated_item(it, "recent") for it in candidates
              if cutoff <= int(it.get("ts") or 0) <= now_ts]
    future = [_decorated_item(it, "future_source_time") for it in candidates
              if int(it.get("ts") or 0) > now_ts]
    undated = [_decorated_item(it, "undated") for it in candidates
               if int(it.get("ts") or 0) <= 0]
    undated_count = len(undated)
    recent.sort(key=lambda it: int(it.get("ts") or 0), reverse=True)
    future.sort(key=lambda it: int(it.get("ts") or 0))

    if statuses:
        sources_tried = len(statuses)
        sources_ok = sum(1 for row in statuses if row.get("status") in ("ok", "empty"))
        sources_with_items = sum(1 for row in statuses if row.get("status") == "ok")
        sources_empty = sum(1 for row in statuses if row.get("status") == "empty")
        failures = [{"source": row.get("source"), "sector": row.get("sector"),
                     "error": row.get("last_error")}
                    for row in statuses if row.get("status") == "error"]
        source_quality = "degraded" if failures else "complete"
    else:
        sources_tried = data.get("sources_tried", data.get("n_sources")) if want is None else None
        sources_ok = data.get("sources_ok") if want is None else None
        sources_with_items = data.get("sources_with_items") if want is None else None
        sources_empty = data.get("sources_empty") if want is None else None
        failures = list(data.get("failures") or []) if want is None else []
        source_quality = "unknown"

    health = _source_health(statuses, now_ts, max_age)
    expired_last_good = sum(
        1 for row in statuses if row.get("freshness_status") == "hard-failure"
        and row.get("last_good_expired"))

    notes = []
    if future:
        notes.append(f"{len(future)} 条源声明未来时刻已从‘近 {days} 天’新闻流剥离；可能是活动日程或源时钟异常。")
    if undated_count:
        notes.append(f"{undated_count} 条缺少可验证时区/时间，未冒充近期新闻。")
    if failures:
        notes.append(f"{len(failures)} 个源本轮失败，其他源结果仍可用。")
    if expired_last_good:
        notes.append(f"{expired_last_good} 个源的 last-good 已过期，相关旧内容未继续配送。")
    return {
        "cache_schema": data.get("cache_schema"),
        "pulled_at": data.get("pulled_at"),
        "retrieved_at": data.get("retrieved_at"),
        "refresh_attempted_at": data.get("refresh_attempted_at"),
        "refresh_attempted_ts": data.get("refresh_attempted_ts"),
        "refresh_deadline_s": data.get("refresh_deadline_s"),
        "refresh_elapsed_ms": data.get("refresh_elapsed_ms"),
        "refresh_deadline_exceeded": bool(data.get("refresh_deadline_exceeded")),
        "n_sources": sources_tried,
        "configured_sources": data.get("n_sources"),
        "sources_tried": sources_tried,
        "sources_ok": sources_ok,
        "sources_with_items": sources_with_items,
        "sources_empty": sources_empty,
        "sources_failed": len(failures) if source_quality != "unknown" else None,
        "source_quality": source_quality,
        "quality": source_quality,
        "source_status": statuses,
        "source_health": health,
        "last_good_max_age_s": max_age,
        "failures": failures,
        "industries": data.get("industries") or [],
        "days": days,
        "sectors": sectors,
        "window": {"start": _iso_utc(cutoff), "end": _iso_utc(now_ts),
                   "basis": "source_declared_time", "future_in_recent": False},
        "n": len(recent[:limit]),
        "total_recent": len(recent),
        "truncated": len(recent) > limit,
        "future_count": len(future),
        "future_items": future[:limit] if include_future else [],
        "future_items_included": bool(include_future),
        "undated_count": undated_count,
        "undated_items": undated[:min(limit, 100)],
        "undated_items_truncated": len(undated) > min(limit, 100),
        "items": recent[:limit],
        "note": " ".join(notes),
    }


def read_radar_for_ingest(
    sectors: Optional[List[str]] = None,
    days: int = 7,
    per_sector_limit: int = 200,
) -> Dict[str, Any]:
    """为 events 入库读取均衡切片；`per_sector_limit` 是每赛道上限而非展示总上限。

    默认展示流按全市场时间倒序截断会被 macro/tech 占满。本入口仍复用同一缓存和
    时间护栏，但逐赛道取数后再合并，避免“已接 events”实际只覆盖少数赛道。
    """
    days = max(1, min(int(days), 30))
    per_sector_limit = max(1, min(int(per_sector_limit), 500))
    # 保持原有定时入库的刷新语义；刷新成功后从完整缓存重切，不受展示 limit 截断。
    read_radar(sectors=None, days=days, limit=1, use_cache=True)
    cached = _read_cache()
    if not cached:
        return {"items": [], "n": 0, "coverage_by_sector": {},
                "note": "NewsRadar 缓存不可用，未生成入库切片。"}
    industries = cached.get("industries") or load_sources()[0]
    allowed = [str(row.get("key") or "") for row in industries if isinstance(row, dict)]
    wanted = [str(value) for value in (sectors or allowed) if str(value) in set(allowed)]
    rows: List[Dict[str, Any]] = []
    coverage: Dict[str, int] = {}
    available: Dict[str, int] = {}
    seen: set[tuple[str, str, int]] = set()
    for sector in wanted:
        part = _slice(cached, [sector], days, per_sector_limit)
        coverage[sector] = int(part.get("n") or 0)
        available[sector] = int(part.get("total_recent") or 0)
        for item in part.get("items") or []:
            identity = (str(item.get("source") or ""), str(item.get("url") or ""),
                        int(item.get("ts") or 0))
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(item)
    rows.sort(key=lambda item: int(item.get("ts") or 0), reverse=True)
    out = _slice(cached, sectors, days, 500)
    out.update({"items": rows, "n": len(rows), "total_recent": len(rows),
                "total_available_recent": sum(available.values()),
                "truncated": any(value > per_sector_limit for value in available.values()),
                "coverage_by_sector": coverage,
                "available_by_sector": available,
                "per_sector_limit": per_sector_limit})
    return out
