# -*- coding: utf-8 -*-
"""NewsRadar 可靠性增量契约：不得破坏主线既有 API/缓存字段。"""
from __future__ import annotations

import json
import io
import multiprocessing
import ssl
import threading
import time
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlsplit

import guanlan_v2.datafeed.newsradar as nr


class _Response:
    def __init__(self, body: str, content_type: str = "application/rss+xml"):
        self._body = body.encode("utf-8")
        self.status = 200
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body


def _rss(title: str, ts: int | None = None) -> str:
    stamp = int(ts if ts is not None else time.time())
    published = format_datetime(datetime.fromtimestamp(stamp, tz=timezone.utc), usegmt=True)
    return (
        "<?xml version='1.0'?><rss><channel><item>"
        f"<title>{title}</title><link>https://items.example/{title}</link>"
        f"<pubDate>{published}</pubDate>"
        "</item></channel></rss>"
    )


def _source(name: str, sector: str, url: str) -> dict:
    return {"name": name, "hint": sector, "url": url}


def _result(source: dict, *, ok: bool, title: str = "") -> dict:
    now = int(time.time())
    items = ([{"title": title, "url": f"https://items.example/{title}", "summary": "",
               "source": source["name"], "sector": source["hint"], "ts": now}]
             if ok and title else [])
    return {
        "source": source["name"], "sector": source["hint"], "url": source["url"],
        "http_status": 200 if ok else None, "content_type": "application/rss+xml",
        "status": "ok" if items else "empty" if ok else "error",
        "error": "" if ok else "TimeoutError: timed out", "error_kind": "" if ok else "timeout",
        "retryable": not ok, "attempts": 1, "items": items, "latency_ms": 1,
        "items_parsed": len(items), "items_seen": len(items), "items_truncated": 0,
        "freshest_source_time": nr._iso_utc(now) if items else "",
    }


def _mp_write_cache_candidate(path: str, payload: dict, gate, delay_s: float = 0.0):
    """spawn 子进程入口：真实经过 NewsRadar 的跨进程锁与择优替换。"""
    import guanlan_v2.datafeed.newsradar as child_nr

    child_nr._CACHE_PATH = Path(path)
    gate.wait(5.0)
    if delay_s:
        time.sleep(delay_s)
    child_nr._write_cache(payload)


def test_transient_retry_is_bounded_and_403_certificate_html_do_not_retry():
    calls = []
    sleeps = []

    def recovers(req, timeout=None, **kwargs):
        calls.append((req.full_url, timeout, kwargs))
        if len(calls) < 3:
            raise urllib.error.URLError(ssl.SSLEOFError(8, "unexpected EOF"))
        return _Response(_rss("recovered"))

    recovered = nr._fetch_source(
        _source("retry", "semi", "https://retry.example/feed"), opener=recovers,
        sleep_fn=sleeps.append, max_attempts=3, timeout_s=0.25)
    assert recovered["status"] == "ok" and recovered["attempts"] == 3
    assert len(calls) == 3 and [call[1] for call in calls] == [0.25] * 3
    assert sleeps == sorted(sleeps) and len(sleeps) == 2
    assert all(call[2].get("context") is None for call in calls)

    cases = [
        (lambda req, **kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 403, "Forbidden", None, None)), "http_403"),
        (lambda req, **kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("CERTIFICATE_VERIFY_FAILED")), "tls_cert_verification"),
        (lambda req, **kwargs: _Response(
            "<!doctype html><html><body>challenge</body></html>", "text/html"),
         "invalid_feed"),
    ]
    for opener, expected_kind in cases:
        blocked_calls = []

        def counted(req, **kwargs):
            blocked_calls.append(req.full_url)
            return opener(req, **kwargs)

        blocked = nr._fetch_source(
            _source("blocked", "tech", "https://blocked.example/feed"),
            opener=counted, sleep_fn=lambda _delay: None, max_attempts=5)
        assert blocked["status"] == "error" and blocked["error_kind"] == expected_kind
        assert blocked["attempts"] == len(blocked_calls) == 1


def test_503_html_waf_error_body_is_inspected_and_never_retried():
    cases = [
        ("https://waf-ctype.example/feed", "text/html; charset=utf-8", b"busy"),
        ("https://waf-body.example/feed", "application/octet-stream",
         b"<!doctype html><html><title>Just a moment</title>cloudflare</html>"),
    ]
    for url, content_type, body in cases:
        calls = []

        def waf(req, **kwargs):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(
                req.full_url, 503, "Service Unavailable",
                {"Content-Type": content_type}, io.BytesIO(body),
            )

        out = nr._fetch_source(
            _source("waf", "tech", url), opener=waf,
            sleep_fn=lambda _delay: (_ for _ in ()).throw(
                AssertionError("HTML/WAF 503 must not back off or retry")),
            max_attempts=5, timeout_s=0.25,
        )

        assert calls == [url]
        assert out["status"] == "error" and out["error_kind"] == "http_503"
        assert out["http_status"] == 503 and out["attempts"] == 1
        assert out["retryable"] is False
        assert out["content_type"] == content_type


def test_success_response_stream_rejects_waf_prefix_and_oversize_before_full_read():
    class StreamingResponse:
        status = 200

        def __init__(self, body, content_type="application/octet-stream", *,
                     content_length=None, fail_on_read=False):
            self.body = body
            self.offset = 0
            self.bytes_read = 0
            self.read_calls = []
            self.fail_on_read = fail_on_read
            self.headers = {"Content-Type": content_type}
            if content_length is not None:
                self.headers["Content-Length"] = str(content_length)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            if self.fail_on_read:
                raise AssertionError("body must not be read")
            self.read_calls.append(size)
            if size is None or size < 0:
                size = len(self.body) - self.offset
            chunk = self.body[self.offset:self.offset + size]
            self.offset += len(chunk)
            self.bytes_read += len(chunk)
            return chunk

    html_by_type = StreamingResponse(
        b"ignored", "text/html; charset=utf-8", fail_on_read=True,
    )
    typed = nr._fetch_source(
        _source("typed-waf", "tech", "https://typed-waf.example/feed"),
        opener=lambda *_args, **_kwargs: html_by_type,
        sleep_fn=lambda _delay: (_ for _ in ()).throw(
            AssertionError("200 HTML must not retry")),
        max_attempts=5,
    )
    assert typed["status"] == "error" and typed["error_kind"] == "invalid_feed"
    assert typed["retryable"] is False and typed["attempts"] == 1
    assert html_by_type.read_calls == []

    challenge = StreamingResponse(
        b"<!doctype html><html><title>Just a moment</title>cloudflare</html>"
        + b"x" * (nr._WAF_PREFIX_BYTES * 4),
    )
    challenged = nr._fetch_source(
        _source("prefix-waf", "tech", "https://prefix-waf.example/feed"),
        opener=lambda *_args, **_kwargs: challenge,
        sleep_fn=lambda _delay: (_ for _ in ()).throw(
            AssertionError("prefix WAF must not retry")),
        max_attempts=5,
    )
    assert challenged["status"] == "error" and challenged["attempts"] == 1
    assert challenged["retryable"] is False
    assert challenge.read_calls == [nr._WAF_PREFIX_BYTES]
    assert challenge.bytes_read == nr._WAF_PREFIX_BYTES

    oversized = StreamingResponse(
        b"", "application/rss+xml",
        content_length=nr._MAX_FEED_BYTES + 1, fail_on_read=True,
    )
    too_large = nr._fetch_source(
        _source("oversize", "tech", "https://oversize.example/feed"),
        opener=lambda *_args, **_kwargs: oversized,
        max_attempts=3,
    )
    assert too_large["status"] == "error" and too_large["attempts"] == 1
    assert too_large["retryable"] is False and oversized.read_calls == []


def test_ambiguous_content_type_feed_named_cloudflare_is_not_false_waf():
    body = _rss("Cloudflare publishes a security update").encode("utf-8")

    class StreamingFeed:
        status = 200
        headers = {"Content-Type": "application/octet-stream"}

        def __init__(self):
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            if size < 0:
                size = len(body) - self.offset
            chunk = body[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    out = nr._fetch_source(
        _source("legit", "security", "https://legit.example/feed"),
        opener=lambda *_args, **_kwargs: StreamingFeed(), max_attempts=1,
    )
    assert out["status"] == "ok" and out["items_parsed"] == 1
    assert out["items"][0]["title"] == "Cloudflare publishes a security update"


def test_scheduler_caps_each_host_keeps_cross_host_parallel_and_prioritizes_semi(monkeypatch):
    sources = [
        _source(f"same-{index}", "tech", f"https://same.example/feed/{index}")
        for index in range(5)
    ] + [_source("other", "consumer", "https://other.example/feed")]
    monkeypatch.setattr(nr, "_MAX_WORKERS", 6)
    monkeypatch.setattr(nr, "_PER_HOST_CONCURRENCY", 2)
    lock = threading.Lock()
    active = defaultdict(int)
    peaks = defaultdict(int)
    total = 0
    total_peak = 0

    def fetch(source):
        nonlocal total, total_peak
        host = urlsplit(source["url"]).hostname or ""
        with lock:
            active[host] += 1
            total += 1
            peaks[host] = max(peaks[host], active[host])
            total_peak = max(total_peak, total)
        time.sleep(0.04)
        with lock:
            active[host] -= 1
            total -= 1
        return _result(source, ok=True, title=source["name"])

    monkeypatch.setattr(nr, "_fetch_source", fetch)
    results = nr._fetch_sources_scheduled(sources)
    assert [row["source"] for row in results] == [source["name"] for source in sources]
    assert peaks["same.example"] == 2 and total_peak >= 3

    order = []
    monkeypatch.setattr(nr, "_MAX_WORKERS", 1)
    priority_sources = [
        _source("consumer", "consumer", "https://consumer.example/feed"),
        _source("chip", "semi", "https://semi.example/feed"),
    ]
    monkeypatch.setattr(
        nr, "_fetch_source",
        lambda source: order.append(source["url"]) or _result(source, ok=True, title="one"),
    )
    nr._fetch_sources_scheduled(priority_sources)
    assert order[0] == "https://semi.example/feed"


def test_refresh_adds_three_state_health_without_replacing_existing_status_contract(
        monkeypatch, tmp_path):
    now = int(time.time())
    fresh = _source("Fresh", "ai", "https://fresh.example/feed")
    fallback = _source("Fallback", "semi", "https://fallback.example/feed")
    hard = _source("Hard", "tech", "https://hard.example/feed")
    old_success = now - 120
    cache = {
        "cache_schema": 2, "pulled_ts": old_success, "pulled_at": "old",
        "retrieved_at": nr._iso_utc(old_success), "n_sources": 1,
        "source_status": [{
            "source": fallback["name"], "sector": fallback["hint"],
            "url": fallback["url"], "status": "ok",
            "last_success_at": nr._iso_utc(old_success), "last_success_ts": old_success,
        }],
        "items": [{
            "title": "known good", "url": "https://items.example/known", "summary": "",
            "source": fallback["name"], "sector": fallback["hint"],
            "source_url": fallback["url"], "ts": now - 60,
        }],
    }
    cache_path = tmp_path / "newsradar.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setattr(nr, "_CACHE_PATH", cache_path)
    monkeypatch.setattr(nr, "load_sources", lambda: ([], [fresh, fallback, hard]))
    monkeypatch.setattr(
        nr, "_fetch_source",
        lambda source: (_result(source, ok=True, title="new")
                        if source["url"] == fresh["url"] else _result(source, ok=False)),
    )

    out = nr.read_radar(use_cache=True, ttl_s=0, last_good_max_age_s=300)

    assert out["source_health"]["counts"] == {
        "fresh": 1, "last-good": 1, "hard-failure": 1}
    by_url = {row["url"]: row for row in out["source_status"]}
    assert by_url[fresh["url"]]["status"] == "ok"
    assert by_url[fallback["url"]]["status"] == "error"
    assert by_url[hard["url"]]["status"] == "error"
    assert by_url[fallback["url"]]["freshness_status"] == "last-good"
    assert by_url[hard["url"]]["freshness_status"] == "hard-failure"
    assert by_url[fallback["url"]]["last_success_ts"] == old_success
    fallback_item = next(row for row in out["items"] if row["title"] == "known good")
    assert fallback_item["source_status"] == "last-good"
    assert fallback_item["source_fetched_ts"] == old_success
    # 主线既有统计/缓存契约仍存在。
    assert out["sources_tried"] == 3 and out["sources_ok"] == 1
    persisted = json.loads(cache_path.read_text(encoding="utf-8"))
    assert persisted["cache_schema"] == 2 and "source_status" in persisted


def test_fresh_cache_hit_rechecks_last_good_expiry_and_never_delivers_it(
        monkeypatch, tmp_path):
    now = int(time.time())
    success = now - 61
    source = _source("Aging", "semi", "https://aging.example/feed")
    payload = {
        "cache_schema": 2, "pulled_ts": now, "pulled_at": "current",
        "last_good_max_age_s": 300, "n_sources": 1, "sources_tried": 1,
        "source_status": [{
            "source": source["name"], "sector": source["hint"], "url": source["url"],
            "status": "error", "last_error": "timeout", "last_success_ts": success,
            "last_success_at": nr._iso_utc(success), "served_from_last_good": True,
            "stale_items_carried": 1,
        }],
        "items": [{
            "title": "expired", "url": "https://items.example/expired", "summary": "",
            "source": source["name"], "sector": source["hint"], "source_url": source["url"],
            "ts": now - 10, "stale_source": True, "source_status": "last-good",
            "source_fetched_ts": success,
        }],
    }
    cache_path = tmp_path / "newsradar.json"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(nr, "_CACHE_PATH", cache_path)
    monkeypatch.setattr(
        nr, "load_sources", lambda: (_ for _ in ()).throw(
            AssertionError("fresh cache hit must not fetch")),
    )

    out = nr.read_radar(use_cache=True, ttl_s=300, last_good_max_age_s=60)

    assert out["items"] == []
    row = out["source_status"][0]
    assert row["status"] == "error" and row["freshness_status"] == "hard-failure"
    assert row["last_good_expired"] is True and row["served_from_last_good"] is False
    assert out["source_health"]["hard-failure"] == 1
    assert out["last_good_max_age_s"] == 60


def test_public_compatibility_surfaces_remain_present():
    import inspect

    read_parameters = inspect.signature(nr.read_radar).parameters
    swr_parameters = inspect.signature(nr.read_radar_swr).parameters
    assert "include_future" in read_parameters and "include_future" in swr_parameters
    assert "force_refresh" in swr_parameters
    assert callable(nr.read_radar_for_ingest)
    _industries, sources = nr.load_sources()
    assert len(sources) == len({source["url"] for source in sources}) == 106


def test_slow_response_read_obeys_strict_attempt_deadline_and_is_closed():
    release = threading.Event()
    closed = threading.Event()

    class _SlowResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            release.wait(2.0)
            return _rss("too late").encode("utf-8")

        def close(self):
            closed.set()

    started = time.monotonic()
    try:
        out = nr._fetch_source(
            _source("slow", "semi", "https://slow.example/feed"),
            opener=lambda req, timeout=None, **kwargs: _SlowResponse(),
            max_attempts=1, timeout_s=0.08)
        elapsed = time.monotonic() - started
        assert elapsed < 0.35
        assert out["status"] == "error" and out["error_kind"] == "timeout"
        assert out["attempts"] == 1 and closed.wait(0.1)
    finally:
        release.set()


def test_round_deadline_cancels_pending_uses_last_good_and_late_reads_cannot_rewrite_cache(
        monkeypatch, tmp_path):
    import urllib.request

    now = int(time.time())
    fallback = _source("Fallback", "semi", "https://slow.example/feed")
    hard = _source("Hard", "tech", "https://pending.example/feed")
    old_success = now - 10
    cache_path = tmp_path / "newsradar.json"
    cache_path.write_text(json.dumps({
        "cache_schema": 2, "pulled_ts": old_success, "pulled_at": "old",
        "retrieved_at": nr._iso_utc(old_success), "n_sources": 1,
        "source_status": [{
            "source": fallback["name"], "sector": fallback["hint"],
            "url": fallback["url"], "status": "ok",
            "last_success_at": nr._iso_utc(old_success),
            "last_success_ts": old_success,
        }],
        "items": [{
            "title": "last good", "url": "https://items.example/last-good",
            "summary": "", "source": fallback["name"], "sector": fallback["hint"],
            "source_url": fallback["url"], "ts": now - 5,
        }],
    }), encoding="utf-8")
    release = threading.Event()
    close_calls = []
    opened = []
    mode = {"fast": False}

    class _SlowResponse:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            release.wait(2.0)
            return _rss("late old round").encode("utf-8")

        def close(self):
            close_calls.append(True)

    def opener(req, timeout=None, **kwargs):
        opened.append(req.full_url)
        return _Response(_rss("new round")) if mode["fast"] else _SlowResponse()

    writes = []
    original_write = nr._write_cache

    def tracked_write(data):
        writes.append(data.get("refresh_attempted_ts"))
        original_write(data)

    monkeypatch.setattr(nr, "_CACHE_PATH", cache_path)
    monkeypatch.setattr(nr, "load_sources", lambda: ([], [fallback, hard]))
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    monkeypatch.setattr(nr, "_write_cache", tracked_write)
    monkeypatch.setattr(nr, "_REFRESH_DEADLINE_S", 0.10)
    monkeypatch.setattr(nr, "_FETCH_TIMEOUT", 5)
    monkeypatch.setattr(nr, "_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(nr, "_MAX_WORKERS", 1)

    started = time.monotonic()
    first = nr.read_radar(use_cache=True, ttl_s=0, last_good_max_age_s=60)
    elapsed = time.monotonic() - started
    assert elapsed < 0.45
    assert first["refresh_deadline_exceeded"] is True
    assert [row["title"] for row in first["items"]] == ["last good"]
    assert first["source_health"]["counts"] == {
        "fresh": 0, "last-good": 1, "hard-failure": 1}
    assert {row["error_kind"] for row in first["source_status"]} == {"refresh_deadline"}
    assert opened == [fallback["url"]]  # pending source was cancelled before network I/O
    assert close_calls and len(writes) == 1

    # A subsequent healthy round owns the cache. Releasing the old daemon read later
    # must not append a write or replace the newer snapshot.
    mode["fast"] = True
    second = nr.read_radar(use_cache=True, ttl_s=0, last_good_max_age_s=60)
    assert second["source_health"]["fresh"] == 2
    assert {row["title"] for row in second["items"]} == {"new round"}
    assert len(writes) == 2
    newer_bytes = cache_path.read_bytes()
    release.set()
    time.sleep(0.12)
    assert len(writes) == 2 and cache_path.read_bytes() == newer_bytes
    assert list(tmp_path.glob("*.tmp")) == []


def test_permanently_blocked_openers_across_rounds_keep_http_threads_bounded(monkeypatch):
    release = threading.Event()

    class _NeverReturns:
        status = 200
        headers = {"Content-Type": "application/rss+xml"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            release.wait()
            return _rss("released").encode("utf-8")

        def close(self):
            # 第三方 close 本身也永久阻塞；close worker 同样必须固定有界。
            release.wait()

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, **kwargs: _NeverReturns())
    monkeypatch.setattr(nr, "_MAX_WORKERS", 1)
    monkeypatch.setattr(nr, "_MAX_ATTEMPTS", 1)
    source = _source("blocked", "semi", "https://blocked-forever.example/feed")

    try:
        # 超过 attempt worker + queue 容量，若仍是每尝试新线程会持续线性增长。
        rounds = nr._HTTP_ATTEMPT_WORKERS + nr._HTTP_ATTEMPT_QUEUE + 6
        started = time.monotonic()
        for _ in range(rounds):
            result = nr._fetch_sources_scheduled([source], deadline_s=0.02)
            assert result[0]["status"] == "error"
        assert time.monotonic() - started < max(4.0, rounds * 0.08)
        time.sleep(0.08)  # 给每轮外层 scheduler 线程退出时间
        attempt_threads = [thread for thread in threading.enumerate()
                           if thread.name.startswith("newsradar_http_attempt_")]
        close_threads = [thread for thread in threading.enumerate()
                         if thread.name.startswith("newsradar_http_close_")]
        scheduler_threads = [thread for thread in threading.enumerate()
                             if thread.name.startswith("newsradar_")
                             and "http_" not in thread.name]
        assert len(attempt_threads) == nr._HTTP_ATTEMPT_WORKERS
        assert len(close_threads) == nr._HTTP_CLOSE_WORKERS
        assert len(scheduler_threads) <= 1
    finally:
        release.set()
        time.sleep(0.12)


def test_multiprocess_overlapping_refreshes_keep_the_most_complete_snapshot(tmp_path):
    cache_path = tmp_path / "newsradar.json"
    context = multiprocessing.get_context("spawn")
    gate = context.Event()

    def candidate(label: str, ok: int, start_ns: int, end_ns: int) -> dict:
        hard = 10 - ok
        return {
            "cache_schema": 2,
            "label": label,
            "pulled_ts": int(time.time()),
            "refresh_attempted_ts": int(time.time()),
            "refresh_started_ns": start_ns,
            "refresh_completed_ns": end_ns,
            "sources_ok": ok,
            "sources_with_items": ok,
            "sources_failed": hard,
            "source_health": {"counts": {
                "fresh": ok, "last-good": 0, "hard-failure": hard,
            }},
            "items": [{"title": f"{label}-{index}"} for index in range(ok)],
        }

    # 时间区间互相重叠；最差候选故意延迟为最后写入请求，不能覆盖最完整快照。
    payloads = [
        (candidate("best", 10, 100, 500), 0.0),
        (candidate("middle", 7, 120, 520), 0.0),
        (candidate("poor-last", 2, 140, 540), 0.15),
    ]
    processes = [context.Process(
        target=_mp_write_cache_candidate,
        args=(str(cache_path), payload, gate, delay),
    ) for payload, delay in payloads]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    final = json.loads(cache_path.read_text(encoding="utf-8"))
    assert final["label"] == "best"
    assert final["sources_ok"] == 10 and len(final["items"]) == 10
    assert list(tmp_path.glob("*.tmp")) == []
