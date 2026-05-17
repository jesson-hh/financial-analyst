from pathlib import Path
import pytest
from financial_analyst.swarm import load_preset


def test_load_stock_deep_dive(tmp_path):
    # depends on Task 34 yaml + all 13 agents registered in tui._ensure_registered
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("stock-deep-dive", memory_root=tmp_path)
    names = [n.agent.NAME for n in nodes]
    assert "quote-fetcher" in names
    assert "report-writer" in names
    assert len(names) == 13


def test_deps_correctly_wired(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("stock-deep-dive", memory_root=tmp_path)
    by_name = {n.agent.NAME: n for n in nodes}
    assert by_name["report-writer"].deps
    for tier1 in ["quote-fetcher", "factor-computer", "model-predictor", "news-reader", "f10-reader"]:
        assert by_name[tier1].deps == []
