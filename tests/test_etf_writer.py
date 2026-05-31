"""Tests for ETF tier-3: EtfRiskOfficer + EtfReportWriter."""
from __future__ import annotations
import asyncio
import json


def _run(c):
    return asyncio.run(c) if asyncio.iscoroutine(c) else c


def _patch(monkeypatch, mod, payload):
    async def _chat(self, *a, **k):
        return {"choices": [{"message": {"content": json.dumps(payload)}}]}

    monkeypatch.setattr(
        mod,
        "LLMClient",
        type("X", (), {"for_agent": staticmethod(lambda n: type("C", (), {"chat": _chat})())}),
        raising=False,
    )


# ---------------------------------------------------------------------------
# EtfRiskOfficer
# ---------------------------------------------------------------------------

def test_cro_negative_only(monkeypatch, tmp_path):
    import financial_analyst.agent.etf.risk_officer as m
    _patch(monkeypatch, m, {
        "risk_score": -1,
        "veto_flags": ["persistent_premium"],
        "position_sizing_advice": "1-3%",
    })
    out = _run(m.EtfRiskOfficer(memory_root=tmp_path)._execute({
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-metrics-fetcher": {},
    }))
    assert -2 <= out["risk_score"] <= 0
    assert "veto_flags" in out
    assert "position_sizing_advice" in out


def test_cro_clamps_positive_score(monkeypatch, tmp_path):
    """LLM returning positive risk_score must be clamped to 0."""
    import financial_analyst.agent.etf.risk_officer as m
    _patch(monkeypatch, m, {
        "risk_score": 2,          # LLM incorrectly positive
        "veto_flags": [],
        "position_sizing_advice": "5-8%",
    })
    out = _run(m.EtfRiskOfficer(memory_root=tmp_path)._execute({
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-metrics-fetcher": {},
    }))
    assert out["risk_score"] <= 0, "CRO risk_score must never be positive"


def test_cro_clamps_below_neg2(monkeypatch, tmp_path):
    """LLM returning < -2 must be clamped to -2."""
    import financial_analyst.agent.etf.risk_officer as m
    _patch(monkeypatch, m, {
        "risk_score": -5,
        "veto_flags": ["leveraged_held_long"],
        "position_sizing_advice": "0%",
    })
    out = _run(m.EtfRiskOfficer(memory_root=tmp_path)._execute({
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-metrics-fetcher": {},
    }))
    assert out["risk_score"] >= -2


def test_cro_schema_fields(monkeypatch, tmp_path):
    """OUTPUT_SCHEMA validation passes with all required fields."""
    import financial_analyst.agent.etf.risk_officer as m
    _patch(monkeypatch, m, {
        "risk_score": -2,
        "veto_flags": ["low_liquidity"],
        "position_sizing_advice": "0%",
    })
    agent = m.EtfRiskOfficer(memory_root=tmp_path)
    result = _run(agent.run({
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-metrics-fetcher": {},
    }))
    assert result.ok, f"run() failed: {result.error}"
    assert result.output.risk_score in range(-2, 1)


# ---------------------------------------------------------------------------
# EtfReportWriter
# ---------------------------------------------------------------------------

def test_writer_writes_and_sums(monkeypatch, tmp_path):
    """Core contract: files written, rating_overall = sum(dims), veto forces 0 position."""
    import financial_analyst.agent.etf.report_writer as m
    payload = {
        "rating_overall": 99,   # intentionally wrong — must be corrected to sum=2
        "rating_dimensions": {
            "holdings": 1,
            "technical": 1,
            "flow": 0,
            "valuation": 1,
            "risk": -1,
        },
        "action": "buy",
        "target_price": 5.2,
        "stop_loss": 4.6,
        "position_pct": 0.05,
        "markdown_body": "# 300ETF 研报\n一、综合评级...",
        "summary_json": {"x": 1},
    }
    _patch(monkeypatch, m, payload)
    inp = {
        "code": "SH510300",
        "asof_date": "2026-05-29",
        "out_dir": str(tmp_path),
        "etf-holdings-analyst": {"holdings_score": 1},
        "etf-technical-analyst": {"technical_score": 1},
        "etf-flow-analyst": {"flow_score": 0},
        "etf-valuation-analyst": {"valuation_score": 1},
        "etf-risk-officer": {"risk_score": -1, "veto_flags": ["persistent_premium"]},
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-quote-fetcher": {},
        "etf-metrics-fetcher": {},
    }
    out = _run(m.EtfReportWriter(memory_root=tmp_path)._execute(inp))

    # rating_overall must equal sum(dims): 1+1+0+1+(-1) = 2  (not 99)
    assert out["rating_overall"] == sum(out["rating_dimensions"].values()), (
        f"rating_overall={out['rating_overall']} != sum(dims)={sum(out['rating_dimensions'].values())}"
    )
    # veto flag present → position_pct must be 0
    assert out["position_pct"] == 0.0, f"veto present but position_pct={out['position_pct']}"
    # files must exist
    assert (tmp_path / "SH510300_2026-05-29.md").exists()
    assert (tmp_path / "SH510300_2026-05-29.json").exists()


def test_writer_rating_zero_forces_no_position(monkeypatch, tmp_path):
    """rating_overall <= 0 (no veto) also forces position_pct=0."""
    import financial_analyst.agent.etf.report_writer as m
    payload = {
        "rating_overall": 0,
        "rating_dimensions": {"holdings": 0, "technical": 0, "flow": 0, "valuation": 0, "risk": 0},
        "action": "hold",
        "target_price": 3.0,
        "stop_loss": 2.8,
        "position_pct": 0.05,  # should be forced to 0
        "markdown_body": "# Test",
        "summary_json": {},
    }
    _patch(monkeypatch, m, payload)
    inp = {
        "code": "SZ159919",
        "asof_date": "2026-05-29",
        "out_dir": str(tmp_path),
        "etf-holdings-analyst": {},
        "etf-technical-analyst": {},
        "etf-flow-analyst": {},
        "etf-valuation-analyst": {},
        "etf-risk-officer": {"risk_score": 0, "veto_flags": []},
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-quote-fetcher": {},
        "etf-metrics-fetcher": {},
    }
    out = _run(m.EtfReportWriter(memory_root=tmp_path)._execute(inp))
    assert out["position_pct"] == 0.0


def test_writer_clamps_position_pct(monkeypatch, tmp_path):
    """position_pct > 0.10 must be clamped down."""
    import financial_analyst.agent.etf.report_writer as m
    payload = {
        "rating_overall": 8,
        "rating_dimensions": {"holdings": 2, "technical": 2, "flow": 2, "valuation": 2, "risk": 0},
        "action": "buy",
        "target_price": 5.5,
        "stop_loss": 4.9,
        "position_pct": 0.50,   # way too high — clamp to 0.10
        "markdown_body": "# Test",
        "summary_json": {},
    }
    _patch(monkeypatch, m, payload)
    inp = {
        "code": "SH510050",
        "asof_date": "2026-05-29",
        "out_dir": str(tmp_path),
        "etf-holdings-analyst": {},
        "etf-technical-analyst": {},
        "etf-flow-analyst": {},
        "etf-valuation-analyst": {},
        "etf-risk-officer": {"risk_score": 0, "veto_flags": []},
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-quote-fetcher": {},
        "etf-metrics-fetcher": {},
    }
    out = _run(m.EtfReportWriter(memory_root=tmp_path)._execute(inp))
    assert out["position_pct"] <= 0.10


def test_writer_files_contain_code(monkeypatch, tmp_path):
    """The written .md file should contain the ETF code somewhere."""
    import financial_analyst.agent.etf.report_writer as m
    payload = {
        "rating_overall": 3,
        "rating_dimensions": {"holdings": 1, "technical": 1, "flow": 1, "valuation": 1, "risk": -1},
        "action": "accumulate",
        "target_price": 4.8,
        "stop_loss": 4.2,
        "position_pct": 0.03,
        "markdown_body": "# SH510300 研报\n内容...",
        "summary_json": {"code": "SH510300"},
    }
    _patch(monkeypatch, m, payload)
    inp = {
        "code": "SH510300",
        "asof_date": "2026-05-30",
        "out_dir": str(tmp_path),
        "etf-holdings-analyst": {},
        "etf-technical-analyst": {},
        "etf-flow-analyst": {},
        "etf-valuation-analyst": {},
        "etf-risk-officer": {"risk_score": -1, "veto_flags": []},
        "etf-bull-advocate": {},
        "etf-bear-advocate": {},
        "etf-quote-fetcher": {},
        "etf-metrics-fetcher": {},
    }
    out = _run(m.EtfReportWriter(memory_root=tmp_path)._execute(inp))
    md_text = (tmp_path / "SH510300_2026-05-30.md").read_text(encoding="utf-8")
    assert "SH510300" in md_text or "300" in md_text
