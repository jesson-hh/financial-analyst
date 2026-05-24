"""Integration tests for per-agent memory_mode (full | retrieval) wiring.

Task 3: verifies that:
- load_preset passes a MemoryIndex to agents configured with memory_mode: retrieval
- load_preset without an index leaves all agents in full mode (backward compat)
- bear-advocate calls load_relevant (not load_all) when an index is set
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from financial_analyst.agent.memory_index import MemoryIndex
from financial_analyst.swarm import load_preset
from financial_analyst.tui import _ensure_registered


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mem_root(tmp_path: Path) -> Path:
    """Create a minimal memory directory tree for tests."""
    mem_root = tmp_path / "memories"
    (mem_root / "_shared").mkdir(parents=True)
    (mem_root / "_shared" / "x.md").write_text("shared content", encoding="utf-8")
    for agent in [
        "bear-advocate",
        "risk-officer",
        "bull-advocate",
        "quote-fetcher",
        "factor-computer",
        "model-predictor",
        "news-reader",
        "f10-reader",
        "fundamental-analyst",
        "technical-analyst",
        "whale-analyst",
        "quant-analyst",
        "report-writer",
    ]:
        d = mem_root / agent
        d.mkdir(parents=True, exist_ok=True)
        (d / "stub.md").write_text(f"stub for {agent}", encoding="utf-8")
    return mem_root


# ---------------------------------------------------------------------------
# Test 1: index passed → retrieval agents get it, full agents don't
# ---------------------------------------------------------------------------


def test_load_preset_with_index_uses_retrieval(tmp_path):
    _ensure_registered()
    mem_root = _make_mem_root(tmp_path)

    idx = MemoryIndex(memory_root=mem_root, db_path=tmp_path / "idx.db")
    idx.rebuild()

    nodes = load_preset("stock-deep-dive", memory_root=mem_root, memory_index=idx)
    by_name = {n.agent.NAME: n for n in nodes}

    # bear-advocate and risk-officer are configured for retrieval → must have index
    assert by_name["bear-advocate"].agent.memory.index is not None, (
        "bear-advocate should have memory.index set"
    )
    assert by_name["risk-officer"].agent.memory.index is not None, (
        "risk-officer should have memory.index set"
    )

    # bull-advocate default full → no index
    assert by_name["bull-advocate"].agent.memory.index is None, (
        "bull-advocate should NOT have memory.index (full mode)"
    )

    # Tier 1 agents default full → no index
    assert by_name["quote-fetcher"].agent.memory.index is None, (
        "quote-fetcher should NOT have memory.index (full mode)"
    )
    assert by_name["factor-computer"].agent.memory.index is None, (
        "factor-computer should NOT have memory.index (full mode)"
    )


# ---------------------------------------------------------------------------
# Test 2: backward compat — no index → all agents in full mode
# ---------------------------------------------------------------------------


def test_load_preset_without_index_works_unchanged(tmp_path):
    """Omitting memory_index → all agents use full mode.
    v1.9.4: +introspector (Tier 4). v1.9.7: +overseas-market-scanner + sector-rotation-analyzer
    (Tier 1). Current count: 16."""
    _ensure_registered()
    mem_root = tmp_path / "memories"
    mem_root.mkdir()

    nodes = load_preset("stock-deep-dive", memory_root=mem_root)  # no index arg
    assert len(nodes) == 16
    for n in nodes:
        assert n.agent.memory.index is None, (
            f"{n.agent.NAME} should have memory.index=None when no index provided"
        )


# ---------------------------------------------------------------------------
# Test 3: bear-advocate calls load_relevant (not load_all) when index is set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bear_advocate_uses_load_relevant_when_index_set(tmp_path):
    """Verify bear-advocate calls load_relevant (not load_all) in retrieval mode."""
    from financial_analyst.agent.tier3.bear_advocate import BearAdvocate

    mem_root = tmp_path / "memories"
    bear_dir = mem_root / "bear-advocate"
    bear_dir.mkdir(parents=True)
    (bear_dir / "pitfalls.md").write_text(
        "F8 super_distr. F9 broken board.", encoding="utf-8"
    )

    idx = MemoryIndex(memory_root=mem_root, db_path=tmp_path / "idx.db")
    idx.rebuild()

    agent = BearAdvocate(memory_root=mem_root, index=idx)

    fake_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "thesis_bullets": ["bear point 1"],
                        "valuation_concerns": [],
                        "technical_breakdown": [],
                        "target_price_low": 100.0,
                        "downside_pct": -0.1,
                        "f_anchors": ["F8"],
                    })
                }
            }
        ]
    }

    with patch(
        "financial_analyst.agent.tier3.bear_advocate.LLMClient.for_agent"
    ) as mock_for:
        client = AsyncMock()
        client.chat = AsyncMock(return_value=fake_response)
        mock_for.return_value = client

        with patch.object(
            agent.memory, "load_relevant", wraps=agent.memory.load_relevant
        ) as spy_rel:
            with patch.object(
                agent.memory, "load_all", wraps=agent.memory.load_all
            ) as spy_all:
                result = await agent.run(
                    {"fundamental-analyst": {"valuation_score": 1}}
                )

    assert result.ok is True, f"agent.run() failed: {result.error}"
    assert spy_rel.called, "load_relevant should be called when index is set"
    # Note: load_relevant may internally call load_all as a 0-hit fallback (Fix 1 v0.2.3);
    # the important invariant is that load_relevant is the entry-point, not raw load_all.
