# -*- coding: utf-8 -*-
"""guanlan_v2.industry — AI投研看板(行业逻辑框架)。

随 cards/seats/screen 先例:导出 build_industry_router,挂在薄壳 create_app 上。
spec: docs/superpowers/specs/2026-07-02-ai-industry-dashboard-design.md
"""
from __future__ import annotations

__all__ = ["build_industry_router"]


def build_industry_router():
    from .api import build_industry_router as _b
    return _b()
