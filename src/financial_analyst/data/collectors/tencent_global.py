"""Tencent global-index collector — 拉国际指数 (qt.gtimg.cn 国内镜像).

跟 tencent_quote.py 同路径 (HTTP GBK, 国内出口, 不撞 Clash MITM). 替代
yfinance — yfinance 用 curl_cffi 跟 Clash MITM 冲突会 TLS 失败.

支持 codes 命名:
- 美股: usDJI (道指) / usIXIC (纳指) / usINX (标普500) / usVIX (恐慌指数)
- 港股: hkHSI (恒生) / hkHSTECH (恒生科技)
- (DXY / 商品 / 汇率 tencent 没, 后续接 sina finance)

字段层级跟 tencent_quote 不同 — 国际指数没 PE/PB/换手率, 只有 OHLC + 涨跌:
  1 name · 2 code · 3 price · 4 change · 5 changePercent · 6 high · 7 low ·
  8 open · 9 prevClose · ...
"""
from __future__ import annotations
import time
from typing import Any, Dict, List, Optional

from financial_analyst.data.net import rate_limited, register_source

# 国际指数限速一档 — 早盘扫一次足够, 不需要高频
register_source("tencent_global", qps=2.0, max_retries=2, backoff_base=1.0, cache_ttl=30.0)

_TENCENT_BASE = "http://qt.gtimg.cn/q="

# 默认国际指数 universe (覆盖核心传导 channel)
DEFAULT_GLOBAL_INDICES = [
    # 美股
    "usDJI",     # 道琼斯工业
    "usIXIC",    # 纳斯达克综合
    "usINX",     # 标普 500
    "usVIX",     # CBOE 波动率指数 (VIX, 风险偏好指标)
    # 港股
    "hkHSI",     # 恒生指数
    "hkHSTECH",  # 恒生科技
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
                # 国际指数 layout 至少 35 个字段 (tencent qt.gtimg.cn); A股是 50+
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
                "change": _f(f[31]),          # ←国际指数 layout: change 在 [31]
                "changePercent": _f(f[32]),   # changePercent 在 [32]
                "high": _f(f[33]),
                "low": _f(f[34]),
                "ts": time.time(),
            }
        return out

    def fetch_default(self) -> Dict[str, Dict[str, Any]]:
        """Convenience: pull the default 6-index universe."""
        return self.fetch(DEFAULT_GLOBAL_INDICES)
