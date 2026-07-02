# -*- coding: utf-8 -*-
"""guanlan_v2.strategy 路径常量(vendored 五层产物/知识落点)。"""
from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).resolve().parent
VENDOR_DIR = _PKG / "vendor"
ARTIFACTS_DIR = VENDOR_DIR / "artifacts"
MODELS_DIR = ARTIFACTS_DIR / "models"
KNOWLEDGE_DIR = VENDOR_DIR / "knowledge"
PROVENANCE_JSON = _PKG / "_provenance.json"

# 选股直接消费的核心产物
V4_RANKING_PARQUET = ARTIFACTS_DIR / "v4_ranking_latest.parquet"
STOCK_BASIC_PARQUET = ARTIFACTS_DIR / "tushare_stock_basic.parquet"
# L4 V1 节奏视角:市场宽度残差面板(lu/amt 残差 + 60 日分位;R27 情绪周期判据)
MARKET_BREADTH_PARQUET = ARTIFACTS_DIR / "market_breadth_resid.parquet"
# regime 因子族动态权重(2026-07-02 spec)四产物
FACTOR_LS_PARQUET = ARTIFACTS_DIR / "factor_ls_returns.parquet"
FACTOR_REGIME_PARQUET = ARTIFACTS_DIR / "factor_regime.parquet"
FACTOR_REGIME_META_JSON = ARTIFACTS_DIR / "factor_regime_meta.json"
FACTOR_REGIME_GATE_JSON = ARTIFACTS_DIR / "factor_regime_gate.json"
# P1 收益回流:全A等权日收益基准(basket_perf 的公平尺;regen 顺算)
EQW_MARKET_RET_PARQUET = ARTIFACTS_DIR / "eqw_market_ret.parquet"
