# -*- coding: utf-8 -*-
"""guanlan 大盘状态模块 —— `market_status.json` 刷新端点(读走引擎 `/watch/market_status`)。"""
from guanlan_v2.market.api import build_market_router, start_market_status_scheduler

__all__ = ["build_market_router", "start_market_status_scheduler"]
