"""Unified network layer for data collectors.

Two architectural pieces:

1. **国内/国外路由分流**: ``domestic_session()`` 绕开系统代理 (国内站必须直连,
   不然走 Clash 的海外节点会卡); ``intl_session()`` 走系统代理 (国外站需要 VPN
   时由 Clash 转发). **翻墙/不翻墙环境都适用** —— 直连国内站本来就该走 direct,
   国外站在不翻墙环境下 trust_env=True 也只是 direct (够不到就够不到, 不是这一
   层的问题).

2. **限速 + 重试 + 可选缓存**: ``@rate_limited("xueqiu")`` 给 collector.fetch
   套上 QPS 上限 + 指数退避 + 可选短 TTL 缓存. 防止前端连点 / 后台轮询 / agent
   突发调用把对端 WAF 触发. 每个 source 单独配置, 统计可由 ``source_stats()``
   暴露给 ``/diag`` 监控.

整个模块**不依赖任何 collector**, 避免循环 import; collector 端只需:

    from financial_analyst.data.net import domestic_session, rate_limited

    class XueqiuCommentsCollector:
        @rate_limited("xueqiu", cache_key=lambda self, code, limit=30: f"c:{code}:{limit}")
        def fetch(self, code, limit=30):
            sess = domestic_session()
            ...

See ``reference_guanlan_ui.md`` 头号守则 (API 稳定性) for the rules this enforces.
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


# ──────────────────────── Sessions: 国内 / 国外 ────────────────────────


def domestic_session(extra_headers: Optional[Dict[str, str]] = None) -> requests.Session:
    """国内站点直连 (xueqiu / 腾讯 / Tushare / Aliyun / eastmoney 等).

    ``trust_env=False`` 让 ``requests`` 忽略 ``HTTP_PROXY/HTTPS_PROXY`` 环境变量,
    避开 Clash 把国内流量错路由到海外节点. 翻墙环境必须用这个; 不翻墙环境用这个
    也无害 (反正没有 proxy env).
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
    """国外站点 (Anthropic / OpenAI / Hugging Face 等).

    ``trust_env=True`` 让 ``requests`` 读 ``HTTP_PROXY/HTTPS_PROXY``. 翻墙环境
    下走 Clash 出去; 不翻墙环境下走直连 (够得到就够得到, 够不到调用方自己处理).
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
    """每个 source 的累计统计, 给 /diag 暴露."""
    calls_total: int = 0
    retries_total: int = 0
    throttled_total: int = 0  # 被限速排队的次数
    cache_hits_total: int = 0
    last_call_ts: float = 0.0
    last_error: str = ""
    last_error_ts: float = 0.0


class _MinIntervalLimiter:
    """最简单可靠的限速: 两次调用最小间隔. 1/QPS 秒.

    线程安全 (collector 跑在 asyncio.to_thread 里); 实现为 sleep 等待, 不抛错."""

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._next_allowed: float = 0.0

    def acquire(self) -> float:
        """阻塞直到允许调用. 返回实际等待秒数 (0 = 没排队)."""
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
    backoff_base: float  # 实际退避 = base * 2^attempt
    cache_ttl: float = 0.0  # 0 = 不缓存
    stats: _SourceStats = field(default_factory=_SourceStats)
    _cache: Dict[Any, tuple] = field(default_factory=dict)  # key → (ts, value)
    _cache_lock: threading.Lock = field(default_factory=threading.Lock)


_SOURCES: Dict[str, _Source] = {}


def register_source(name: str, qps: float = 1.0,
                    max_retries: int = 2, backoff_base: float = 2.0,
                    cache_ttl: float = 0.0) -> None:
    """注册一个 source 的策略. 幂等 (重复 register 会覆盖配置, 但保留 stats).

    Args:
        name: source 标识, 跟 ``@rate_limited(name)`` 对应.
        qps: 每秒最多多少次. min_interval = 1/qps. 0 = 不限速.
        max_retries: 失败后最多再试几次 (0 = 不重试).
        backoff_base: 退避秒数基数. attempt n 等 base * 2^n 秒.
        cache_ttl: 命中缓存的时间窗 (秒). 0 = 不缓存. 需要 ``@rate_limited``
            的 ``cache_key`` 参数也给出, 否则缓存不生效.
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
    """识别可重试的故障: 限速 / 临时网络抖 / 服务侧 5xx / 空 list 静默限速."""
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
    """装饰 collector.fetch (或任何外部调用), 套上限速 / 退避 / 缓存.

    Args:
        source_name: 必须先 ``register_source(source_name, ...)``. 未注册时
            装饰器透传 (不限速, 不重试, 不缓存) — 保证 collector 在导入顺序
            混乱时仍能工作, 出问题再补 register.
        cache_key: 可选, ``fn(*args, **kwargs) -> hashable``. 配合 source 的
            ``cache_ttl>0`` 才生效. ``self`` 也会传给 cache_key.

    Returns:
        包装后的函数. 抛出原异常 (重试用完后).
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
                    ck = None  # cache_key 自己挂了不能拖死真请求
                if ck is not None:
                    with src._cache_lock:
                        hit = src._cache.get(ck)
                    if hit and (time.time() - hit[0]) < src.cache_ttl:
                        src.stats.cache_hits_total += 1
                        return hit[1]

            # 2. rate limit (block 到允许)
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
                    # 缓存写入
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
            # 理论不可达, 但 mypy 喜欢
            if last_exc:
                raise last_exc
            return None
        return wrapper
    return deco


def source_stats() -> Dict[str, Dict[str, Any]]:
    """所有已注册 source 的累计统计. 给 /diag 序列化用."""
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


# ──────────────────────── 预注册已知 sources ────────────────────────
#
# QPS / 重试 / 缓存值是经验起点, 看 /diag 监控数据再调.
#
#   xueqiu        — Aliyun WAF 反爬较严格, 限到 1/s, 缓存 30s 避免连点
#   xueqiu_hot    — 单独一档因为可以更宽松 (公共榜单, 不是 per-stock)
#   tencent_quote — 国内, 宽松, 行情高频
#   tushare       — 服务侧自有限速 (token 200次/分), 客户端再限一遍
#   eastmoney     — opencli HTTP 源, 稳, 不限速 (qps=0 不限)
#
register_source("xueqiu",             qps=1.0, max_retries=2, backoff_base=2.0, cache_ttl=30.0)
register_source("xueqiu_hot",         qps=2.0, max_retries=2, backoff_base=2.0, cache_ttl=60.0)
register_source("tencent_quote",      qps=5.0, max_retries=1, backoff_base=1.0, cache_ttl=2.0)
register_source("tushare",            qps=2.0, max_retries=2, backoff_base=3.0, cache_ttl=0.0)
register_source("eastmoney_kuaixun",  qps=2.0, max_retries=1, backoff_base=1.0, cache_ttl=15.0)
register_source("eastmoney_longhu",   qps=1.0, max_retries=1, backoff_base=2.0, cache_ttl=600.0)
register_source("eastmoney_holders",  qps=1.0, max_retries=1, backoff_base=2.0, cache_ttl=600.0)
register_source("sinafinance",        qps=2.0, max_retries=1, backoff_base=2.0, cache_ttl=30.0)
# ths_hot 还是 opencli browser-mode (跟旧 xueqiu 浏览器桥一样有反爬风险), 限严点
register_source("ths_hot",            qps=1.0, max_retries=2, backoff_base=3.0, cache_ttl=120.0)
