"""Tencent real-time quote collector (qt.gtimg.cn).

Lightweight HTTP — NOT opencli/browser. One request fetches dozens of
stocks (comma-separated), ~120ms, GBK-encoded, no cookie. This is the
right data source for high-frequency monitoring walls / alert sweeps,
where opencli's 2-5s/code browser bridge can't keep up.

Field layout of ``v_sh600519="1~贵州茅台~600519~1311~..."`` (~ split),
verified against live data 2026-05:
  1 name · 2 code · 3 price · 4 prevClose · 5 open · 6 volume(lots)
  31 change · 32 changePct · 33 high · 34 low · 37 amount(10K yuan) ·
  38 turnover% · 39 pe · 43 amplitude% · 44 circ_mv(100M yuan) ·
  45 total_mv(100M yuan) · 46 pb · 49 vol_ratio (量比, volume ratio)
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Optional

from financial_analyst.data.net import rate_limited
from financial_analyst.data.code_norm import etf_exchange

_TENCENT_BASE = "http://qt.gtimg.cn/q="


def _to_tencent(code: str) -> str:
    """SH600519 → sh600519 · 600519 → sh600519 (best-effort prefix)."""
    c = str(code).upper().strip()
    if "." in c:
        num, _, suf = c.partition(".")
        if suf in ("SH", "SZ", "BJ"):
            return suf.lower() + num
        c = num
    if c[:2] in ("SH", "SZ", "BJ"):
        return c[:2].lower() + c[2:]
    if c.isdigit() and len(c) == 6:
        ex = etf_exchange(c)
        if ex:
            return ex.lower() + c
        if c[0] == "6":
            return "sh" + c
        if c[0] in "03":
            return "sz" + c
        if c[0] in "84":
            return "bj" + c
    return c.lower()


def _norm(code: str) -> str:
    """Tencent code (sh600519) → canonical SH600519 for dict keys."""
    c = str(code).strip()
    if c[:2].lower() in ("sh", "sz", "bj"):
        return c[:2].upper() + c[2:]
    return c.upper()


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class TencentQuoteCollector:
    """Batch real-time quotes. ``fetch(codes)`` → ``{SH600519: {...}, ...}``."""

    @rate_limited("tencent_quote",
                  cache_key=lambda self, codes, timeout=6.0:
                  tuple(sorted({str(c).upper() for c in codes})))
    def fetch(self, codes: List[str], timeout: float = 6.0) -> Dict[str, Dict[str, Any]]:
        if not codes:
            return {}
        # httpx.Client(trust_env=False) already isolates locally — do not
        # setdefault NO_PROXY globally (would pollute the huggingface / litellm overseas path)
        import httpx
        tc = [_to_tencent(c) for c in codes]
        url = _TENCENT_BASE + ",".join(tc)
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            resp = client.get(url)
        resp.raise_for_status()
        raw = resp.content.decode("gbk", errors="replace")
        parsed = self._parse(raw)
        # Also key by each input code as-given (600519, SH600519, sz300750…)
        # so callers that stored a non-canonical code still find their quote.
        for c in codes:
            canon = _norm(_to_tencent(c))
            if canon in parsed and c not in parsed:
                parsed[c] = parsed[canon]
        return parsed

    def quote(self, code: str) -> Optional[Dict[str, Any]]:
        """Single-stock convenience. Returns one dict or None."""
        d = self.fetch([code])
        return next(iter(d.values()), None) if d else None

    @staticmethod
    def _parse(raw: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for stmt in raw.split(";"):
            stmt = stmt.strip()
            if not stmt or "=" not in stmt or '"' not in stmt:
                continue
            try:
                inner = stmt.split('"', 2)[1]
            except IndexError:
                continue
            f = inner.split("~")
            if len(f) < 50 or not f[2]:
                continue
            code = _norm("sh" + f[2] if False else f[2])  # use raw code below
            # The statement key carries the prefix: v_sh600519
            key = stmt.split("=", 1)[0].strip()
            prefix = key.replace("v_", "")[:2]  # sh/sz/bj
            canonical = prefix.upper() + f[2]
            out[canonical] = {
                "code": canonical,
                "name": f[1],
                "price": _f(f[3]),
                "prevClose": _f(f[4]),
                "open": _f(f[5]),
                "volume": _f(f[6]),          # lots (手)
                "change": _f(f[31]),
                "changePercent": _f(f[32]),  # %
                "high": _f(f[33]),
                "low": _f(f[34]),
                "amount": _f(f[37]),         # 10K yuan (万)
                "turnover_rate": _f(f[38]),  # %
                "pe": _f(f[39]),
                "amplitude": _f(f[43]),      # %
                "circ_mv": _f(f[44]),        # 100M yuan (亿)
                "total_mv": _f(f[45]),       # 100M yuan (亿)
                "pb": _f(f[46]),
                "vol_ratio": _f(f[49]),      # volume ratio (量比)
                "asof": (f[30] or None),     # trade datetime 'YYYYMMDDHHMMSS' (盘口时间, 供实时 UI)
            }
        return out
