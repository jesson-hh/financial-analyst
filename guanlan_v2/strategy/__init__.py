# -*- coding: utf-8 -*-
"""guanlan_v2.strategy — vendored 五层选股体系(仓内自有事实源)。

版本戳见 ``_PROVENANCE.md`` / ``_provenance.json``;设计见
``docs/superpowers/specs/2026-06-05-strategy-vendoring-design.md``。

Option 1(已对齐):**消费 v4 排名产物**(py3.13 装不了 qlib,计算暂留外部);
未来相位移植到引擎 loader 自算(qlib_to_zoo 译 34 表达式)。本期独立于其他界面,
不与 chat/cards/factor/seats/graph 做信息交互。
"""
from __future__ import annotations

from guanlan_v2.strategy.decision import apply_shields, converge, rate_v4
from guanlan_v2.strategy.perspectives import (
    market_cycle,
    nine_view_scan,
    resonance_count,
)
from guanlan_v2.strategy.ranking import (
    V4_COLUMNS,
    load_v4_ranking,
    mainline_status_map,
    name_industry_map,
    ranking_date,
    ts_to_qlib,
)

__all__ = [
    "V4_COLUMNS",
    "load_v4_ranking",
    "mainline_status_map",
    "name_industry_map",
    "ranking_date",
    "ts_to_qlib",
    # L4 九视角
    "market_cycle",
    "nine_view_scan",
    "resonance_count",
    # L5 决策层
    "rate_v4",
    "apply_shields",
    "converge",
]
