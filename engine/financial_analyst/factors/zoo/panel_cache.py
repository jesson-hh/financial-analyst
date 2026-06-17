"""LRU 面板缓存 — 同一 (codes, 窗口, freq) 只加载一次, 交互式多因子复用。

线程安全 (server 把 sync 端点跑在线程池)。慢加载在锁外执行, 仅 OrderedDict
读写持锁; 同 key 并发首次可能重复加载一次 (幂等, 不影响正确性)。

调用方对返回的面板**只读** (build_report / compose 产新 Series, 不 in-place
改面板) — 故多调用方共享同一缓存面板是安全的。
"""
from __future__ import annotations
import hashlib
import threading
from collections import OrderedDict
from typing import List

from financial_analyst.factors.zoo.panel import PanelData

_MAXSIZE = 3                       # 每面板 ~50-100MB → 上限 ~300MB
_cache: "OrderedDict[tuple, PanelData]" = OrderedDict()
_lock = threading.Lock()


def _key(codes: List[str], start: str, end: str, freq: str, with_industry: bool) -> tuple:
    h = hashlib.md5(",".join(sorted(codes)).encode("utf-8")).hexdigest()
    return (h, start, end, freq, with_industry)


def load_panel_cached(loader, codes: List[str], start: str, end: str,
                      freq: str = "day", industry_loader=None) -> PanelData:
    """命中则复用缓存面板, 否则 PanelData.from_loader 加载并存入 (LRU)。"""
    k = _key(codes, start, end, freq, industry_loader is not None)
    with _lock:
        hit = _cache.get(k)
        if hit is not None:
            _cache.move_to_end(k)
            return hit
    panel = PanelData.from_loader(loader, codes, start, end, freq=freq,
                                  industry_loader=industry_loader)
    with _lock:
        _cache[k] = panel
        _cache.move_to_end(k)
        while len(_cache) > _MAXSIZE:
            _cache.popitem(last=False)
    return panel


def clear_panel_cache() -> None:
    with _lock:
        _cache.clear()
