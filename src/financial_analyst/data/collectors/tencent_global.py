"""Tencent global-index collector — pull international indices (qt.gtimg.cn domestic mirror).

Same path as tencent_quote.py (HTTP GBK, domestic egress, does not collide with Clash MITM).
Replaces yfinance — yfinance uses curl_cffi which clashes with Clash MITM and fails TLS.

Supported code naming:
- US: usDJI (Dow) / usIXIC (Nasdaq) / usINX (S&P 500) / usVIX (fear index)
- HK: hkHSI (Hang Seng) / hkHSTECH (Hang Seng Tech)
- (DXY / commodities / FX not provided by Tencent; later via sina finance)

Field layout differs from tencent_quote — international indices have no PE/PB/turnover,
only OHLC + change:
  1 name · 2 code · 3 price · 4 change · 5 changePercent · 6 high · 7 low ·
  8 open · 9 prevClose · ...
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional

from financial_analyst.data.net import rate_limited, register_source

# Rate-limit one tier for international indices — one pre-open scan is enough, no high frequency needed
register_source("tencent_global", qps=2.0, max_retries=2, backoff_base=1.0, cache_ttl=30.0)

_TENCENT_BASE = "http://qt.gtimg.cn/q="

# Default international-index universe (covers the core transmission channels)
DEFAULT_GLOBAL_INDICES = [
    # US
    "usDJI",     # Dow Jones Industrial
    "usIXIC",    # Nasdaq Composite
    "usINX",     # S&P 500
    "usVIX",     # CBOE Volatility Index (VIX, risk-appetite indicator)
    # HK
    "hkHSI",     # Hang Seng Index
    "hkHSTECH",  # Hang Seng Tech
]


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TencentGlobalCollector:
    """Batch global indices. ``fetch(codes)`` → ``{usDJI: {...}, ...}``."""

    @rate_limited(
        "tencent_global",
        cache_key=lambda self, codes=None, timeout=8.0:
        tuple(sorted({str(c) for c in (codes or DEFAULT_GLOBAL_INDICES)})),
    )
    def fetch(self, codes: Optional[List[str]] = None,
              timeout: float = 8.0) -> Dict[str, Dict[str, Any]]:
        """Fetch global indices. Default = 6 core indices (DJI/IXIC/INX/VIX/HSI/HSTECH)."""
        codes = codes or DEFAULT_GLOBAL_INDICES
        if not codes:
            return {}
        import httpx
        url = _TENCENT_BASE + ",".join(codes)
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content.decode("gbk", errors="replace")
        return self._parse(raw, codes)

    @staticmethod
    def _parse(raw: str, requested: List[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for stmt in raw.split(";"):
            stmt = stmt.strip()
            if not stmt or "=" not in stmt or '"' not in stmt:
                continue
            try:
                inner = stmt.split('"', 2)[1]
            except IndexError:
                continue
            if not inner:
                continue
            f = inner.split("~")
            if len(f) < 35:
                # Global-index layout has at least 35 fields (tencent qt.gtimg.cn); A-share is 50+
                continue
            # tencent key e.g. v_usDJI / v_hkHSI
            key = stmt.split("=", 1)[0].strip()
            code = key.replace("v_", "")
            out[code] = {
                "code": code,
                "name": f[1],
                "price": _f(f[3]),
                "prevClose": _f(f[4]),
                "open": _f(f[5]),
                "change": _f(f[31]),          # ← global-index layout: change at [31]
                "changePercent": _f(f[32]),   # changePercent at [32]
                "high": _f(f[33]),
                "low": _f(f[34]),
                "ts": time.time(),
            }
        return out

    def fetch_default(self) -> Dict[str, Dict[str, Any]]:
        """Convenience: pull the default 6-index universe."""
        return self.fetch(DEFAULT_GLOBAL_INDICES)
