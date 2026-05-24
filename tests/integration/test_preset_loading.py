from pathlib import Path
import pytest
from financial_analyst.swarm import load_preset


def test_load_stock_deep_dive(tmp_path):
    # v1.9.7: 16 agents (was 14) — added overseas-market-scanner +
    # sector-rotation-analyzer to Tier-1, Tier-2 analysts 接 them as
    # optional macro/sector context.
    # (4 tiers: 7 data + 4 analyst + 4 decision + 1 introspector post-mortem)
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("stock-deep-dive", memory_root=tmp_path)
    names = [n.agent.NAME for n in nodes]
    assert "quote-fetcher" in names
    assert "report-writer" in names
    assert "introspector" in names    # Tier-4 added in v1.9.4
    assert "overseas-market-scanner" in names     # v1.9.7 added
    assert "sector-rotation-analyzer" in names    # v1.9.7 added
    assert len(names) == 16


def test_deps_correctly_wired(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("stock-deep-dive", memory_root=tmp_path)
    by_name = {n.agent.NAME: n for n in nodes}
    assert by_name["report-writer"].deps
    for tier1 in ["quote-fetcher", "factor-computer", "model-predictor", "news-reader", "f10-reader"]:
        assert by_name[tier1].deps == []
