# -*- coding: utf-8 -*-
"""guanlan_v2.events —— 确定性零 LLM 全局归一事件库。

SQLite 单库 ``var/events/events.db``（WAL）承载 append-only 事件、来源运行审计、
关系审计与 PIT 检索面。v3 统一 source/source_asof/event_time/available_at、
issuer/context 与幂等键；旧大库经 ``normalized_events`` 视图兼容，不在启动时
全表回填。Console 与 ``/gl-mcp`` 工具只读；关键词只形成可审计候选，不自动
升级为发行人事实。详见 ``docs/events_research_p0.md``。
"""
from __future__ import annotations
