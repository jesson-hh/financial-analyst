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
from financial_analyst.data.loaders.technical import technical_definition_fingerprint

PANEL_CACHE_VERSION = "panel_causal_tech_v2"
_MAXSIZE = 3                       # 每面板 ~50-100MB → 上限 ~300MB
_cache: "OrderedDict[tuple, PanelData]" = OrderedDict()
_lock = threading.Lock()


class _IdentityKey:
    """Keep a strong identity reference, including for unhashable stub loaders."""
    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return id(self.value)

    def __eq__(self, other):
        return isinstance(other, _IdentityKey) and self.value is other.value


def _source_key(loader, codes, freq):
    # Only stat the three technical-input files and calendar; no binary hashing
    # or unrelated universe scan. This stops an old panel hiding loader refresh.
    roots = getattr(loader, "_roots", None)
    if isinstance(roots, dict) and freq in roots:
        identity = (type(loader).__module__, type(loader).__qualname__, str(roots[freq].resolve()))
    else:
        identity = _IdentityKey(loader)
    signatures = ()
    if freq == "day" and callable(getattr(loader, "_tech_source_signatures", None)):
        signatures = tuple((code, loader._tech_source_signatures(code)) for code in sorted(codes))
    return identity, signatures


def _key(codes: List[str], start: str, end: str, freq: str, with_industry: bool,
         source_key=None) -> tuple:
    h = hashlib.md5(",".join(sorted(codes)).encode("utf-8")).hexdigest()
    tech_hash = technical_definition_fingerprint() if freq == "day" else None
    return (PANEL_CACHE_VERSION, tech_hash, source_key, h, start, end, freq, with_industry)


def load_panel_cached(loader, codes: List[str], start: str, end: str,
                      freq: str = "day", industry_loader=None) -> PanelData:
    """命中则复用缓存面板, 否则 PanelData.from_loader 加载并存入 (LRU)。"""
    source_key = _source_key(loader, codes, freq)
    k = _key(codes, start, end, freq, industry_loader is not None, source_key)
    with _lock:
        hit = _cache.get(k)
        if hit is not None:
            _cache.move_to_end(k)
            return hit
    panel = PanelData.from_loader(loader, codes, start, end, freq=freq,
                                  industry_loader=industry_loader)
    if _source_key(loader, codes, freq) != source_key:
        raise RuntimeError("Panel technical inputs changed while loading; retry on a stable snapshot")
    with _lock:
        _cache[k] = panel
        _cache.move_to_end(k)
        while len(_cache) > _MAXSIZE:
            _cache.popitem(last=False)
    return panel


def clear_panel_cache() -> None:
    with _lock:
        _cache.clear()
