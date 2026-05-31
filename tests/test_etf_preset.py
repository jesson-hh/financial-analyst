"""Tests for the etf-deep-dive swarm preset (B-Task 5)."""
from pathlib import Path
import pytest

from financial_analyst.swarm.loader import load_preset


def test_etf_preset_loads(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("etf-deep-dive", memory_root=tmp_path)
    names = {n.agent.NAME for n in nodes}
    assert "etf-report-writer" in names
    assert "etf-holdings-analyst" in names
    assert "etf-introspector" in names


def test_etf_preset_report_writer_deps(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("etf-deep-dive", memory_root=tmp_path)
    rw = next(n for n in nodes if n.agent.NAME == "etf-report-writer")
    assert {"etf-bull-advocate", "etf-bear-advocate", "etf-risk-officer"} <= set(rw.deps)
    assert {
        "etf-holdings-analyst",
        "etf-technical-analyst",
        "etf-flow-analyst",
        "etf-valuation-analyst",
    } <= set(rw.deps)


def test_etf_preset_tier1_has_no_deps(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("etf-deep-dive", memory_root=tmp_path)
    by_name = {n.agent.NAME: n for n in nodes}
    for tier1 in ["etf-quote-fetcher", "etf-metrics-fetcher", "overseas-market-scanner"]:
        assert by_name[tier1].deps == [], f"{tier1} should have no deps"


def test_etf_preset_introspector_deps(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("etf-deep-dive", memory_root=tmp_path)
    intr = next(n for n in nodes if n.agent.NAME == "etf-introspector")
    assert "etf-report-writer" in intr.deps
    assert "etf-holdings-analyst" in intr.deps


def test_etf_preset_node_count(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    nodes = load_preset("etf-deep-dive", memory_root=tmp_path)
    # 13 agents total: 3 tier-1 + 1 tier-1.5 (sector-rotation-analyzer) +
    # 4 tier-2 analysts + bull + bear + risk + report-writer + introspector
    assert len(nodes) == 13


def test_etf_preset_memory_mode_bear(tmp_path):
    from financial_analyst.tui import _ensure_registered
    _ensure_registered()
    # bear-advocate should have memory_mode: retrieval in YAML (loader passes index)
    # Just verifying the preset loads without error when memory_index=None
    nodes = load_preset("etf-deep-dive", memory_root=tmp_path, memory_index=None)
    names = {n.agent.NAME for n in nodes}
    assert "etf-bear-advocate" in names
