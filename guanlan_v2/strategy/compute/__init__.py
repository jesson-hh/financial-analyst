# -*- coding: utf-8 -*-
"""引擎原生计算层 —— 把五层漏斗的上游产物从 qlib 包迁到引擎二进制读取(py3.13 可跑)。

子模块:
- ``breadth``   市场宽度(节奏)面板 + 残差(迁 r27 + ic_probe)
- ``mainline``  主线雷达板块面板 + 月级状态(迁 build_sector_panel + compute_monthly_mainlines)
- ``v4``        v4 排名(38 因子 + LGB + 顶200 评分,迁 v4_ranking.py;统计等价)
"""
from guanlan_v2.strategy.compute import breadth  # noqa: F401
from guanlan_v2.strategy.compute import mainline  # noqa: F401
from guanlan_v2.strategy.compute import v4  # noqa: F401

__all__ = ["breadth", "mainline", "v4"]
