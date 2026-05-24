"""Multi-source realtime quote with automatic fallback chain.

v1.9.6 — vibe-trading 借鉴的唯一新增点: 数据接口多源 fallback. 实时行情 tencent
失败时不该让 buddy / agent 直接 fail, 应该自动 fallback 雪球 (cookie 慢但活).

设计:
- 每条 source 是一个 ``(name, fetcher)`` 二元组. fetcher 返 dict 或 None.
- 上层顺序调用, 第一个返非空且 ``price`` (或 ``current``) 非 None 就返.
- 全失败抛 ``RuntimeError`` + 每源错误明细 (便于诊断哪源挂).
- **不缓存**: 各 source 内部已有 ``@rate_limited`` 缓存 (xueqiu 30s, tencent 2s).
  fallback 层只做 routing, 不重复 cache.

集成方:
- ``buddy/tools.py::_tool_realtime_quote`` (单股, 已有手写 fallback → 改用此 helper)
- ``buddy/tools.py::_tool_quote_batch`` (批量, 之前只 tencent, 现加 xueqiu fallback)
- ``buddy/tools.py`` 内 ``stock_brief`` 段也是手写, 可后续重构, 但风险较高暂不动.
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple


# ──────────────────────── single-stock fetchers ────────────────────────

def _fetch_tencent_single(code: str) -> Optional[Dict[str, Any]]:
    """腾讯 qt.gtimg.cn — 无 cookie ~120ms, 字段全 (price/pe/pb/vol_ratio/mc)."""
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
    return TencentQuoteCollector().quote(code)


def _fetch_xueqiu_single(code: str) -> Optional[Dict[str, Any]]:
    """雪球 opencli browser bridge — 需 cookie, ~2s, 有 market_status + 盘口."""
    from financial_analyst.data.collectors.opencli.xueqiu_stock import XueqiuStockCollector
    return XueqiuStockCollector().fetch(code)


# ──────────────────────── batch fetchers ────────────────────────

def _fetch_tencent_batch(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """腾讯一次拉几十只 ~120ms."""
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector
    return TencentQuoteCollector().fetch(codes)


def _fetch_xueqiu_batch(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """雪球只支持单股, 这里循环模拟批量 (退化场景, 慢但保命)."""
    from financial_analyst.data.collectors.opencli.xueqiu_stock import XueqiuStockCollector
    coll = XueqiuStockCollector()
    out: Dict[str, Dict[str, Any]] = {}
    for c in codes:
        try:
            q = coll.fetch(c)
            if q:
                out[c] = q
        except Exception:
            pass  # 单股失败不破整 batch
    return out


# ──────────────────────── default chains ────────────────────────

SingleFetcher = Callable[[str], Optional[Dict[str, Any]]]
BatchFetcher = Callable[[List[str]], Dict[str, Dict[str, Any]]]

DEFAULT_SINGLE_SOURCES: List[Tuple[str, SingleFetcher]] = [
    ("tencent", _fetch_tencent_single),
    ("xueqiu", _fetch_xueqiu_single),
]

DEFAULT_BATCH_SOURCES: List[Tuple[str, BatchFetcher]] = [
    ("tencent", _fetch_tencent_batch),
    ("xueqiu", _fetch_xueqiu_batch),
]


# ──────────────────────── public API ────────────────────────


def _is_valid_quote(q: Optional[Dict[str, Any]]) -> bool:
    """quote 视为有效: 非 None + 有 price (腾讯) 或 current (雪球)."""
    if not q:
        return False
    return q.get("price") is not None or q.get("current") is not None


def fetch_realtime_quote(
    code: str,
    sources: Optional[List[Tuple[str, SingleFetcher]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """单股实时行情多源 fallback.

    Args:
        code: 股票代码 (SH600519/600519/sh600519 都接受, source 各自归一).
        sources: 自定义 fallback 链, 默认 DEFAULT_SINGLE_SOURCES.

    Returns:
        ``(source_name, quote_dict)``. source_name 是实际成功的那源.

    Raises:
        RuntimeError: 全部源失败, message 含每源错误.
    """
    chain = sources or DEFAULT_SINGLE_SOURCES
    errors: List[str] = []
    for name, fn in chain:
        try:
            q = fn(code)
            if _is_valid_quote(q):
                return name, q  # type: ignore[return-value]
            errors.append(f"{name}: empty/no-price")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")
    raise RuntimeError(
        f"all realtime quote sources failed for {code}: {'; '.join(errors)}"
    )


def fetch_realtime_quotes(
    codes: List[str],
    sources: Optional[List[Tuple[str, BatchFetcher]]] = None,
) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """批量实时行情多源 fallback. 第一个 source 返非空就用 (不强求覆盖全 codes).

    Returns:
        ``(source_name, {code: quote_dict, ...})``.

    Raises:
        RuntimeError: 全部源失败.
    """
    chain = sources or DEFAULT_BATCH_SOURCES
    errors: List[str] = []
    for name, fn in chain:
        try:
            d = fn(codes)
            if d:  # 任何 source 返了至少 1 只 quote 就算成
                return name, d
            errors.append(f"{name}: empty")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")
    raise RuntimeError(
        f"all batch quote sources failed for {len(codes)} codes: {'; '.join(errors)}"
    )
