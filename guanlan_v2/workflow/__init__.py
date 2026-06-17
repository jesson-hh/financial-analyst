# -*- coding: utf-8 -*-
"""guanlan 自有后端 · workflow(工作流节点)。

随 cards / seats / factorlib 先例,挂在薄壳 ``create_app`` 上。一个
``build_workflow_router()`` 容纳 P2-P5 所有自有节点端点(P2 ``/feature/build``;
P3 ``/model/*``;P5 ``/backtest/*`` 往同一 router 追加)。

引擎 primitive(``PanelData`` / ``compile_factor`` / 预处理 / ``ic_analysis`` /
``forward_simple_returns``)全部**函数体内延迟 import**(对齐 factorlib/seats 先例),
不在模块顶部 import 引擎,保证路由组在引擎缺失时仍可构造。数据走 ``get_default_loader``
→ ``load_panel_cached``(数据根全由引擎 ``get_data_paths`` 解析,零硬编码)。

诚实失败:异常 / 空 universe / 空面板 → ``ok:False`` + ``reason``,HTTP 200
(前端降级,不抛 500)。

公共 API::

    from guanlan_v2.workflow import build_workflow_router  # /feature/build(P2)路由组(工厂式)
"""
from __future__ import annotations

from guanlan_v2.workflow.api import build_workflow_router

__all__ = ["build_workflow_router"]
