"""watch/news.py — 盯盘实时新闻源 (eastmoney 7x24 快讯 → 按股票过滤).

The 盯盘 loop's news channel needs a ``callable(code) -> list[str]`` returning
today's realtime headlines mentioning that stock. This wraps the public 7x24
快讯 collector (:class:`EastmoneyKuaixunCollector`, no token) into exactly that:

* **one global pull per refresh window** (TTL-cached) — the loop calls the
  provider once *per watched code* each news-tick, so without caching we'd hit
  the网 N times for the identical feed; the cache makes it one pull shared
  across every code in the tick;
* **per-code filter** — keep items whose ``stocks`` field contains the 6-digit
  symbol (mirrors the collector's own ``collect()`` filter), OR — when a
  ``{code: name}`` map is supplied — whose title/summary mentions the name;
* return the matched **titles** (feed order = newest first), capped at
  ``max_headlines``.

Fully **defensive**: a missing ``opencli`` / network failure / parse error
yields ``[]`` (never raises) so the news channel degrades to silent-off without
ever killing a tick — consistent with the rest of the watch package (spec §10).
The loop additionally offloads the call to a thread (it is a *blocking* opencli
subprocess), so nothing here needs to be async.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(r"\d{6}")


def _symbol_of(code: str) -> str:
    """6-digit core of a stock code (``SH600519`` / ``600519.SH`` / ``600519`` → ``600519``)."""
    m = _SYMBOL_RE.search(code or "")
    return m.group(0) if m else ""


class KuaixunNewsProvider:
    """Callable news source for the 盯盘 loop: ``provider(code) -> [headline, ...]``.

    Wraps a 7x24 快讯 collector, caches the global feed for ``refresh_seconds``,
    filters per code. Construct once per watch session; pass as
    ``WatchLoop(news_provider=...)``.

    Args:
        collector: object with ``fetch(limit) -> list[dict]`` (default: a lazily
            constructed :class:`EastmoneyKuaixunCollector`).
        max_headlines: cap on titles returned per code.
        refresh_seconds: TTL of the cached global feed (monotonic clock).
        names: optional ``{code|symbol: 名称}`` to also match by name in
            title/summary (improves recall when ``stocks`` omits the code).
        fetch_limit: how many 快讯 to pull per refresh.
        enabled: tri-state — ``None`` auto-detects via ``opencli`` availability;
            ``False`` short-circuits every call to ``[]`` without touching the网.
    """

    def __init__(
        self,
        collector: Any = None,
        max_headlines: int = 10,
        refresh_seconds: float = 90.0,
        names: Optional[Dict[str, str]] = None,
        fetch_limit: int = 200,
        enabled: Optional[bool] = None,
    ) -> None:
        self._collector = collector
        self._max = int(max_headlines)
        self._refresh = float(refresh_seconds)
        self._names = dict(names or {})
        self._fetch_limit = int(fetch_limit)
        if enabled is None:
            enabled = self._detect_enabled()
        self._enabled = bool(enabled)
        self._cache: List[Dict[str, Any]] = []
        self._cache_ts: float = 0.0
        self._fetched_once: bool = False

    # ─────────────────────── availability ───────────────────────

    @staticmethod
    def _detect_enabled() -> bool:
        """``True`` unless we can positively determine ``opencli`` is absent.

        When the runner is importable we trust its ``is_opencli_available`` check;
        otherwise we let the (defensive) fetch decide rather than mis-disabling.
        """
        try:
            from financial_analyst.data.collectors.opencli.runner import (
                is_opencli_available,
            )
        except Exception:  # noqa: BLE001
            return True
        return bool(is_opencli_available())

    def _get_collector(self) -> Any:
        if self._collector is None:
            from financial_analyst.data.collectors.opencli.eastmoney_kuaixun import (
                EastmoneyKuaixunCollector,
            )
            self._collector = EastmoneyKuaixunCollector()
        return self._collector

    # ─────────────────────── feed cache ───────────────────────

    def _refresh_feed(self) -> None:
        """Pull the global 快讯 feed if the cache is stale. Never raises."""
        now = time.monotonic()
        if self._fetched_once and (now - self._cache_ts) < self._refresh:
            return
        try:
            items = self._get_collector().fetch(limit=self._fetch_limit)
            self._cache = list(items or [])
        except Exception as exc:  # noqa: BLE001 — degrade to silent-off
            log.warning("KuaixunNewsProvider: feed refresh failed (%s); news off this window", exc)
            # keep the last good cache; still bump ts so we don't hammer the网
            # once per watched code within this window.
        self._cache_ts = now
        self._fetched_once = True

    # ─────────────────────── per-code filter ───────────────────────

    def headlines(self, code: str) -> List[str]:
        """Today's realtime headlines mentioning ``code`` (titles, capped)."""
        if not self._enabled:
            return []
        self._refresh_feed()
        if not self._cache:
            return []
        symbol = _symbol_of(code)
        name = self._names.get(code) or self._names.get(symbol) or ""
        out: List[str] = []
        for it in self._cache:
            if not isinstance(it, dict):
                continue
            stocks = str(it.get("stocks") or "")
            title = str(it.get("title") or "")
            summary = str(it.get("summary") or "")
            hit = bool(symbol and symbol in stocks)
            if not hit and name:
                hit = (name in title) or (name in summary)
            if hit and title:
                out.append(title)
                if len(out) >= self._max:
                    break
        return out

    def __call__(self, code: str) -> List[str]:
        return self.headlines(code)


def make_default_news_provider(
    names: Optional[Dict[str, str]] = None,
) -> Optional[KuaixunNewsProvider]:
    """Build the production news provider, or ``None`` if news can't be sourced.

    Returns ``None`` when ``opencli`` is unavailable so the loop leaves the news
    channel disabled (``news_provider=None``) rather than retrying a doomed pull
    every news-tick.
    """
    try:
        from financial_analyst.data.collectors.opencli.runner import (
            is_opencli_available,
        )
        if not is_opencli_available():
            return None
    except Exception:  # noqa: BLE001 — can't tell → still build (it self-disables)
        pass
    return KuaixunNewsProvider(names=names)


__all__ = ["KuaixunNewsProvider", "make_default_news_provider", "_symbol_of"]
