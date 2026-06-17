"""Unified network layer for data collectors.

Two architectural pieces:

1. **Domestic vs. overseas route split**: ``domestic_session()`` bypasses the
   system proxy (domestic sites MUST direct-connect, otherwise Clash routes them
   via overseas nodes and stalls); ``intl_session()`` honours the system proxy
   (overseas sites need Clash forwarding when VPN is required). **Works in both
   VPN-on and VPN-off environments** — direct-connecting to domestic sites
   should always be direct anyway, and overseas sites with trust_env=True in
   VPN-off env still go direct (unreachable means unreachable, not this layer's
   problem).

2. **Rate-limit + retry + optional cache**: ``@rate_limited("xueqiu")`` wraps
   collector.fetch with a QPS cap + exponential backoff + optional short TTL
   cache. Prevents front-end click-spam / background polling / agent burst calls
   from triggering the upstream WAF. Configured per-source; stats surfaced via
   ``source_stats()`` for ``/diag`` monitoring.

The whole module **depends on no collector** to avoid circular imports; the
collector side only needs:

    from financial_analyst.data.net import domestic_session, rate_limited

    class XueqiuCommentsCollector:
        @rate_limited("xueqiu", cache_key=lambda self, code, limit=30: f"c:{code}:{limit}")
        def fetch(self, code, limit=30):
            sess = domestic_session()
            ...

See ``reference_guanlan_ui.md`` first principle (API stability) for the rules this enforces.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, Optional

import requests

_DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_DEFAULT_LANG = "zh-CN,zh;q=0.9"


# ──────────────────────── Sessions: domestic / overseas ────────────────────────


def domestic_session(extra_headers: Optional[Dict[str, str]] = None) -> requests.Session:
    """Direct-connect for domestic sites (xueqiu / Tencent / Tushare / Aliyun / eastmoney etc).

    ``trust_env=False`` makes ``requests`` ignore the ``HTTP_PROXY/HTTPS_PROXY`` env vars,
    avoiding Clash mis-routing domestic traffic to overseas nodes. Required in VPN-on
    environments; harmless in VPN-off environments (no proxy env to read anyway).
    """
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": _DEFAULT_UA,
        "Accept-Language": _DEFAULT_LANG,
    })
    if extra_headers:
        s.headers.update(extra_headers)
    return s


def intl_session(extra_headers: Optional[Dict[str, str]] = None) -> requests.Session:
    """Overseas sites (Anthropic / OpenAI / Hugging Face etc).

    ``trust_env=True`` lets ``requests`` read ``HTTP_PROXY/HTTPS_PROXY``. In VPN-on
    environments it goes out via Clash; in VPN-off environments it direct-connects
    (reachable or not is the caller's problem, not this layer's).
    """
    s = requests.Session()
    s.trust_env = True
    s.headers.update({
        "User-Agent": _DEFAULT_UA,
    })
    if extra_headers:
        s.headers.update(extra_headers)
    return s


# ──────────────────────── Rate limiter + retry + cache ────────────────


@dataclass
class _SourceStats:
    """Cumulative stats per source, surfaced to /diag."""
    calls_total: int = 0
    retries_total: int = 0
    throttled_total: int = 0  # number of times the call was rate-limit queued
    cache_hits_total: int = 0
    last_call_ts: float = 0.0
    last_error: str = ""
    last_error_ts: float = 0.0


class _MinIntervalLimiter:
    """Simplest reliable rate-limit: minimum interval between two calls. 1/QPS seconds.

    Thread-safe (collectors run inside asyncio.to_thread); implemented as a sleep
    wait, never raises."""

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_allowed: float = 0.0

    def acquire(self) -> float:
        """Block until the call is allowed. Returns actual wait seconds (0 = no queue)."""
        if self.min_interval <= 0:
            return 0.0
        with self._lock:
            now = time.time()
            wait = max(0.0, self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self.min_interval
        if wait > 0:
            time.sleep(wait)
        return wait


@dataclass
class _Source:
    name: str
    limiter: _MinIntervalLimiter
    max_retries: int
    backoff_base: float  # actual backoff = base * 2^attempt
    cache_ttl: float = 0.0  # 0 = no caching
    stats: _SourceStats = field(default_factory=_SourceStats)
    _cache: Dict[Any, tuple] = field(default_factory=dict)  # key → (ts, value)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)


_SOURCES: Dict[str, _Source] = {}


def register_source(name: str, qps: float = 1.0,
                    max_retries: int = 2, backoff_base: float = 2.0,
                    cache_ttl: float = 0.0) -> None:
    """Register the policy for one source. Idempotent (re-registering overrides
    the config but keeps the stats).

    Args:
        name: source identifier, matches the ``@rate_limited(name)`` name.
        qps: max calls per second. min_interval = 1/qps. 0 = no rate limit.
        max_retries: max retries after failure (0 = no retry).
        backoff_base: backoff base in seconds. attempt n waits base * 2^n seconds.
        cache_ttl: cache hit window (seconds). 0 = no cache. The ``cache_key``
            argument of ``@rate_limited`` must also be provided, otherwise the
            cache does not kick in.
    """
    existing = _SOURCES.get(name)
    src = _Source(
        name=name,
        limiter=_MinIntervalLimiter(1.0 / qps if qps > 0 else 0.0),
        max_retries=max_retries,
        backoff_base=backoff_base,
        cache_ttl=cache_ttl,
        stats=existing.stats if existing else _SourceStats(),
    )
    _SOURCES[name] = src


def _is_retryable(exc: BaseException) -> bool:
    """Identify a retryable failure: rate-limit / transient network blip / server-side 5xx / empty-list silent throttle."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        return True
    s = str(exc).lower()
    if "429" in s or "rate limit" in s or "throttle" in s or "too many" in s:
        return True
    if "503" in s or "502" in s or "504" in s:
        return True
    if "timeout" in s or "timed out" in s:
        return True
    return False


def rate_limited(source_name: str,
                 cache_key: Optional[Callable[..., Any]] = None):
    """Decorate collector.fetch (or any external call) with rate-limit / backoff / cache.

    Args:
        source_name: must call ``register_source(source_name, ...)`` first.
            If unregistered, the decorator passes through (no limit, no retry, no cache)
            — keeps collectors working even when import order is messed up;
            register later when something breaks.
        cache_key: optional, ``fn(*args, **kwargs) -> hashable``. Only effective
            when the source's ``cache_ttl>0``. ``self`` is also passed to cache_key.

    Returns:
        The wrapped function. Raises the original exception (after retries exhausted).
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            src = _SOURCES.get(source_name)
            if src is None:
                return fn(*args, **kwargs)

            # 1. cache lookup
            ck = None
            if cache_key is not None and src.cache_ttl > 0:
                try:
                    ck = cache_key(*args, **kwargs)
                except Exception:
                    ck = None  # if cache_key itself fails, don't drag down the real request
                if ck is not None:
                    with src._cache_lock:
                        hit = src._cache.get(ck)
                    if hit and (time.time() - hit[0]) < src.cache_ttl:
                        src.stats.cache_hits_total += 1
                        return hit[1]

            # 2. rate limit (block until allowed)
            wait = src.limiter.acquire()
            if wait > 0:
                src.stats.throttled_total += 1

            # 3. retry loop
            last_exc: Optional[BaseException] = None
            for attempt in range(src.max_retries + 1):
                try:
                    src.stats.calls_total += 1
                    src.stats.last_call_ts = time.time()
                    result = fn(*args, **kwargs)
                    # cache write
                    if ck is not None and src.cache_ttl > 0:
                        with src._cache_lock:
                            src._cache[ck] = (time.time(), result)
                    return result
                except Exception as e:
                    last_exc = e
                    src.stats.last_error = f"{type(e).__name__}: {str(e)[:120]}"
                    src.stats.last_error_ts = time.time()
                    if attempt < src.max_retries and _is_retryable(e):
                        src.stats.retries_total += 1
                        time.sleep(src.backoff_base * (2 ** attempt))
                        continue
                    raise
            # Theoretically unreachable, but mypy likes it
            if last_exc:
                raise last_exc
            return None
        return wrapper
    return deco


def _clear_all_caches() -> None:
    """Test helper: drop the per-source response cache.

    Called by conftest autouse fixture between tests so a cached value
    from one test doesn't leak into another (when both call the same
    collector with the same args)."""
    for src in _SOURCES.values():
        with src._cache_lock:
            src._cache.clear()


def source_stats() -> Dict[str, Dict[str, Any]]:
    """Cumulative stats for all registered sources. Serialized into /diag."""
    now = time.time()
    out: Dict[str, Dict[str, Any]] = {}
    for name, src in _SOURCES.items():
        s = src.stats
        out[name] = {
            "qps_cap": (1.0 / src.limiter.min_interval) if src.limiter.min_interval > 0 else None,
            "cache_ttl_s": src.cache_ttl,
            "calls": s.calls_total,
            "retries": s.retries_total,
            "throttled": s.throttled_total,
            "cache_hits": s.cache_hits_total,
            "last_call_ago_s": int(now - s.last_call_ts) if s.last_call_ts else None,
            "last_error": s.last_error or None,
            "last_error_ago_s": int(now - s.last_error_ts) if s.last_error_ts else None,
        }
    return out


# ──────────────────────── Pre-register known sources ────────────────────────
#
# QPS / retry / cache values are empirical starting points; tune from /diag stats.
#
#   xueqiu        — Aliyun WAF anti-scrape is strict, limit to 1/s, 30s cache to avoid click spam
#   xueqiu_hot    — separate bucket since it can be looser (public ranking, not per-stock)
#   tencent_quote — domestic, loose, high-frequency quotes
#   tushare       — server-side rate limit (token 200/min), client side rate-limits as well
#   eastmoney     — opencli HTTP source, stable, no rate-limit (qps=0 = unlimited)
#
register_source("xueqiu",             qps=1.0, max_retries=2, backoff_base=2.0, cache_ttl=30.0)
register_source("xueqiu_hot",         qps=2.0, max_retries=2, backoff_base=2.0, cache_ttl=60.0)
register_source("tencent_quote",      qps=5.0, max_retries=1, backoff_base=1.0, cache_ttl=2.0)
register_source("tushare",            qps=2.0, max_retries=2, backoff_base=3.0, cache_ttl=0.0)
register_source("eastmoney_kuaixun",  qps=2.0, max_retries=1, backoff_base=1.0, cache_ttl=15.0)
register_source("eastmoney_longhu",   qps=1.0, max_retries=1, backoff_base=2.0, cache_ttl=600.0)
register_source("eastmoney_holders",  qps=1.0, max_retries=1, backoff_base=2.0, cache_ttl=600.0)
register_source("sinafinance",        qps=2.0, max_retries=1, backoff_base=2.0, cache_ttl=30.0)
# ths_hot is still opencli browser-mode (same anti-scrape risk as the old xueqiu browser bridge), keep it strict
register_source("ths_hot",            qps=1.0, max_retries=2, backoff_base=3.0, cache_ttl=120.0)
