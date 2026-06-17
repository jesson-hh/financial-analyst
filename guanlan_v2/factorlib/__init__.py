# -*- coding: utf-8 -*-
"""guanlan 自有后端 · factorlib(因子库)。

guanlan 自有的因子库(随 cards / seats 先例,挂在薄壳 ``create_app`` 上):

- ``base/*.json``   迁移来的**基础因子**(引擎 zoo-DSL 表达式 + 元数据;由 stocks 的
                    Qlib-DSL 挖掘产物经 ``qlib_to_zoo`` 译写并逐条校验而来,台账见 README)。
- ``mined/*.json``  **自挖落点**(占位;与引擎 ``UserFactorStore`` 衔接 —— 引擎炼出的
                    用户因子仍走 ``/factor/save``→user 库,本目录是 guanlan 库的自挖分层)。

启动时 ``register_library_factors()`` 把 base/mined 因子(经引擎 primitive 校验+编译)
注册进**引擎运行期 zoo registry**(进程级全局 dict,不改 engine/ 文件),使其立即出现在
引擎 ``/factor/list`` 的 ``registered``;``build_factorlib_router()`` 暴露 ``/factorlib/*``
自有端点。数据只经包内 JSON + 引擎 ``get_data_paths``(求值时 panel 才碰本地 qlib bin)。

公共 API::

    from guanlan_v2.factorlib import (
        register_library_factors,   # 启动期注册进 zoo registry,返回 {registered, skipped, ...}
        build_factorlib_router,     # /factorlib/* 路由组(工厂式)
        LibraryFactorStore,         # 读 base/mined + 注册/列因子
    )
"""
from __future__ import annotations

from typing import Any, Dict

from guanlan_v2.factorlib.api import build_factorlib_router
from guanlan_v2.factorlib.store import LibraryFactorStore

__all__ = ["build_factorlib_router", "register_library_factors", "LibraryFactorStore"]


def register_library_factors() -> Dict[str, Any]:
    """启动期把库因子注册进引擎运行期 zoo registry。

    幂等、不崩:内部逐条 ``unregister``→``register``,单条失败只记台账。
    返回 ``{registered, skipped, total, ledger}``(供 server 启动日志打印计数)。

    须在 ``_ensure_engine_importable()`` 之后调用(server.create_app 的插入点天然满足);
    内部会先 ``import financial_analyst.factors.zoo`` 触发内置三族注册。
    """
    return LibraryFactorStore().register_all()
