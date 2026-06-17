# -*- coding: utf-8 -*-
"""guanlan 自有后端 · screen(选股)。

随 cards / seats / factorlib / workflow 先例,挂在薄壳 ``create_app`` 上。一个
``build_screen_router()`` 暴露 ``POST /screen/run``:把前端约束(因子+权重+TopN+行业中性
+流动性/剔除)编译成「最新截面打分→约束→行业中性→TopN→分布统计」,返回与前端
``window.xgBuild`` 同形的结果(``{ok,date,chosen,benched,pool,scored,stat}``)。

引擎 primitive(``resolve_universe_codes`` / ``get_default_loader`` / ``load_panel_cached`` /
``compile_factor`` / ``zscore``)**全部函数体内延迟 import**(对齐 workflow/factorlib/seats
先例),不在模块顶部 import 引擎,保证路由组在引擎缺失时仍可构造。数据走引擎
``get_data_paths``(零硬编码 stocks 路径)。

诚实失败:异常 / 空 universe / 空面板 / 无可用因子 → ``ok:False`` + ``reason``,HTTP 200
(前端降级,不抛 500)。

公共 API::

    from guanlan_v2.screen import build_screen_router  # /screen/* 路由组(工厂式)
"""
from __future__ import annotations

from guanlan_v2.screen.api import build_screen_router

__all__ = ["build_screen_router"]
