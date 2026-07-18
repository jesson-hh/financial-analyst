"""Phase 5 market-context package (contracts + compute).

Added task-by-task; Task 1 ships the market-factor contract half. Re-exports the
public contract surface so consumers can import from
``guanlan_v2.orchestration.market`` without reaching into ``factors``.
"""
from __future__ import annotations

from guanlan_v2.orchestration.market.factors import (
    CoverageSummary,
    FactorProvenance,
    FactorSummary,
    MarketFactorDefinition,
    MarketFactorPoint,
    MarketFactorReport,
    MarketFactorSetSpec,
    MarketFactorValue,
    assemble_market_factor_report,
    build_market_factor_set_v1,
)

__all__ = [
    "CoverageSummary",
    "FactorProvenance",
    "FactorSummary",
    "MarketFactorDefinition",
    "MarketFactorPoint",
    "MarketFactorReport",
    "MarketFactorSetSpec",
    "MarketFactorValue",
    "assemble_market_factor_report",
    "build_market_factor_set_v1",
]
