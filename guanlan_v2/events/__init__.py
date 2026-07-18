# -*- coding: utf-8 -*-
"""guanlan_v2.events —— 确定性零 LLM 事件库(小 phase 乙 · events store v1)。

SQLite 单库 `var/events/events.db`(WAL)承载 append-only 事件表 + PIT 检索面。
纯加法、零 orchestration 依赖(D10 守护测试钉死)、零 LLM(v1 不做语义抽取)。
详见 docs/superpowers/plans/2026-07-18-events-store-v1.md。
"""
from __future__ import annotations
