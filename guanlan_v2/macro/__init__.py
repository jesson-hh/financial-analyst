# -*- coding: utf-8 -*-
"""全球情绪温度计:PolyMarket+Kalshi 全球宏观预期概率 × A股本土打板温度。

随 industry 先例:导出 build_macro_router,挂在薄壳 create_app 上。
spec: docs/superpowers/specs/2026-07-06-macro-sentiment-thermometer-design.md
"""

__all__ = ["build_macro_router"]


def build_macro_router():
    from .api import build_macro_router as _b
    return _b()
