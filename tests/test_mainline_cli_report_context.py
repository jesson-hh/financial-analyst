# -*- coding: utf-8 -*-
"""CLI keeps the mainline report evidence visible but bounded."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import financial_analyst.agent.orchestrator as orchestrator_module
import financial_analyst.cli as cli
import financial_analyst.swarm as swarm_module
import financial_analyst.tui as tui_module
from financial_analyst.buddy import tools as buddy_tools


def test_run_mainline_prints_bounded_context_citations_and_actions(tmp_path, monkeypatch):
    citations = [
        {
            "citation_id": f"R{idx}",
            "publish_ts": f"2026-08-{idx + 7:02d}T09:30:00+08:00",
            "institution": f"机构{idx}",
            "title": f"行业研报标题{idx}" + ("很长" * 80),
            "source_ref": f"https://example.invalid/report/{idx}?" + ("x" * 220),
            "view": "正文不允许由CLI输出",
        }
        for idx in range(1, 8)
    ]
    output = SimpleNamespace(
        output_md_path=str(tmp_path / "mainline_2026-08-13.md"),
        headline="量价候选保持，研报只作条件交叉验证",
        report_context_status="partial",
        report_context_reason="attached_with_partial_sources",
        report_context_asof="2026-08-15",
        report_citations=citations,
        quant_state={"panel_asof": "2026-08-13", "report_independent": True},
        actionable_signals=[f"行动信号{idx}" + ("观察" * 80) for idx in range(1, 8)],
    )
    captured_inputs = []

    class FakeOrchestrator:
        def __init__(self, nodes):
            assert nodes == ["sealed-node"]

        async def run(self, base_inputs):
            captured_inputs.append(base_inputs)
            return {"mainline-writer": SimpleNamespace(ok=True, output=output)}

    lines = []
    monkeypatch.setattr(orchestrator_module, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(swarm_module, "load_preset", lambda *args, **kwargs: ["sealed-node"])
    monkeypatch.setattr(tui_module, "_ensure_registered", lambda: None)
    monkeypatch.setattr(cli, "default_memory_root", lambda: tmp_path / "memory")
    monkeypatch.setattr(cli.typer, "echo", lambda value="": lines.append(str(value)))

    asyncio.run(cli._run_mainline(
        asof="2026-08-15", panel=None, out_dir=tmp_path / "out",
    ))

    text = "\n".join(lines)
    assert captured_inputs == [{
        "asof_date": "2026-08-15",
        "out_dir": str(tmp_path / "out"),
    }]
    assert "Report context: status=partial; reason=attached_with_partial_sources; asof=2026-08-15" in text
    assert "Narrative headline (mixed inference; not pure quant):" in text
    assert "Quant state (authoritative; reports excluded):" in text
    assert '"report_independent":true' in text
    assert "Report citations (6):" in text
    assert "[R1]" in text and "[R6]" in text and "[R7]" not in text
    assert "https://example.invalid/report/1" in text
    assert "Actionable signals (5):" in text
    assert "行动信号1" in text and "行动信号5" in text and "行动信号6" not in text
    assert "正文不允许由CLI输出" not in text
    assert "…" in text
    assert len(text) < 3000


def test_mainline_tool_recovers_exact_evidence_from_writer_json(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    md = out_dir / "mainline_2026-08-13.md"
    md.write_text("report", encoding="utf-8")
    md.with_suffix(".json").write_text(__import__("json").dumps({
        "report_context": {"items": [{
            "citation_id": "R1", "publish_ts": "2026-08-14",
            "institution": "测试证券", "title": "AIDC 跟踪",
            "source_ref": "https://example.invalid/exact.pdf",
            "source_kind": "live_metadata_only", "view": "元数据观点",
        }]},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(buddy_tools, "_project_root", lambda: tmp_path)

    rows = buddy_tools._mainline_evidence_from_output("Brief: out/mainline_2026-08-13.md")
    assert len(rows) == 1
    assert rows[0]["evidence_id"].startswith("R-")
    assert rows[0]["source_ref"] == "https://example.invalid/exact.pdf"
    assert rows[0]["aliases"] == ["R1"]
