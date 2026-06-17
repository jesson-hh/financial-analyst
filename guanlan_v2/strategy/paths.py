# -*- coding: utf-8 -*-
"""guanlan_v2.strategy 路径常量(vendored 五层产物/知识落点)。"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).resolve().parent
VENDOR_DIR = _PKG / "vendor"
ARTIFACTS_DIR = VENDOR_DIR / "artifacts"
KNOWLEDGE_DIR = VENDOR_DIR / "knowledge"
PROVENANCE_JSON = _PKG / "_provenance.json"

# 选股直接消费的核心产物
V4_RANKING_PARQUET = ARTIFACTS_DIR / "v4_ranking_latest.parquet"
STOCK_BASIC_PARQUET = ARTIFACTS_DIR / "tushare_stock_basic.parquet"
# L4 V1 节奏视角:市场宽度残差面板(lu/amt 残差 + 60 日分位;R27 情绪周期判据)
MARKET_BREADTH_PARQUET = ARTIFACTS_DIR / "market_breadth_resid.parquet"
