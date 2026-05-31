"""Test that ETF agent memory files load correctly via AgentMemory."""
from __future__ import annotations
from pathlib import Path
import financial_analyst


def _repo_root() -> Path:
    # financial_analyst/__init__.py is at src/financial_analyst/__init__.py
    # parents: [0]=financial_analyst, [1]=src, [2]=worktree root
    return Path(financial_analyst.__file__).resolve().parents[2]


def test_writer_memory_loads():
    """etf-report-writer memory must load and contain AUM-tier content."""
    mem_root = _repo_root() / "memories"
    from financial_analyst.agent.etf.report_writer import EtfReportWriter

    a = EtfReportWriter(memory_root=mem_root)
    text = a.memory.load_all()
    assert text, "load_all() returned empty string — check memories/etf-report-writer/ exists"
    assert "AUM" in text, "Expected 'AUM' in etf-report-writer memory (rating system mentions AUM-tier)"


def test_bull_advocate_memory_loads():
    """etf-bull-advocate memory must load V-anchors."""
    mem_root = _repo_root() / "memories"
    from financial_analyst.agent.etf.bull_advocate import EtfBullAdvocate

    a = EtfBullAdvocate(memory_root=mem_root)
    text = a.memory.load_all()
    assert text, "load_all() empty — check memories/etf-bull-advocate/"
    assert "V1" in text, "Expected V-anchor references in etf-bull-advocate memory"


def test_bear_advocate_memory_loads():
    """etf-bear-advocate memory must load F-anchors."""
    mem_root = _repo_root() / "memories"
    from financial_analyst.agent.etf.bear_advocate import EtfBearAdvocate

    a = EtfBearAdvocate(memory_root=mem_root)
    text = a.memory.load_all()
    assert text, "load_all() empty — check memories/etf-bear-advocate/"
    assert "F1" in text, "Expected F-anchor references in etf-bear-advocate memory"


def test_risk_officer_memory_loads():
    """etf-risk-officer memory must load veto rules."""
    mem_root = _repo_root() / "memories"
    from financial_analyst.agent.etf.risk_officer import EtfRiskOfficer

    a = EtfRiskOfficer(memory_root=mem_root)
    text = a.memory.load_all()
    assert text, "load_all() empty — check memories/etf-risk-officer/"
    assert "veto" in text.lower(), "Expected veto rules in etf-risk-officer memory"


def test_seed_matches_dev():
    """Seed dir must contain same ETF agent directories as dev memories dir."""
    root = _repo_root()
    dev_mem = root / "memories"
    seed_mem = root / "src" / "financial_analyst" / "_resources" / "memories_seed"

    etf_agents = [
        "etf-report-writer",
        "etf-bull-advocate",
        "etf-bear-advocate",
        "etf-valuation-analyst",
        "etf-flow-analyst",
        "etf-risk-officer",
    ]
    for agent in etf_agents:
        dev_dir = dev_mem / agent
        seed_dir = seed_mem / agent
        assert dev_dir.exists(), f"dev memories/{agent}/ missing"
        assert seed_dir.exists(), f"seed memories_seed/{agent}/ missing"
        dev_files = {p.name for p in dev_dir.glob("*.md")}
        seed_files = {p.name for p in seed_dir.glob("*.md")}
        assert dev_files == seed_files, (
            f"{agent}: dev={dev_files} != seed={seed_files}"
        )


def test_shared_etf_context_loads():
    """_shared/etf_context.md must exist and load via any ETF agent."""
    root = _repo_root()
    mem_root = root / "memories"
    from financial_analyst.agent.etf.report_writer import EtfReportWriter

    a = EtfReportWriter(memory_root=mem_root)
    text = a.memory.load_all()
    assert "ETF" in text, "_shared/etf_context.md should put 'ETF' in load_all() output"
