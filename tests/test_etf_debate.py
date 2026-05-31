"""Tests for ETF tier-3 debate agents: etf-bull-advocate + etf-bear-advocate."""
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
# Bull advocate
# ---------------------------------------------------------------------------

def test_bull_returns_valid_output(monkeypatch, tmp_path):
    from financial_analyst.agent.etf.bull_advocate import EtfBullAdvocate
    import financial_analyst.agent.etf.bull_advocate as m
    _patch(
        monkeypatch,
        m,
        {
            "thesis_bullets": ["[V1] 半导体国产化顺风，赛道景气持续", "[V3] 折价 0.5%，安全垫明确"],
            "target_price_high": 5.2,
            "target_price_base": 5.0,
            "disproof_signals": ["连续净流出超 3 日", "折溢价转正"],
        },
    )
    out = _run(EtfBullAdvocate(tmp_path)._execute({"etf-holdings-analyst": {"holdings_score": 1}}))

    assert len(out["thesis_bullets"]) >= 2
    assert all(b.startswith("[V") for b in out["thesis_bullets"])
    assert "target_price_base" in out
    assert "target_price_high" in out
    assert out["target_price_base"] > 0


def test_bull_v_anchor_prefix(monkeypatch, tmp_path):
    from financial_analyst.agent.etf.bull_advocate import EtfBullAdvocate
    import financial_analyst.agent.etf.bull_advocate as m
    _patch(
        monkeypatch,
        m,
        {
            "thesis_bullets": ["[V2] 持续净流入动量强劲", "[V6] 规模大流动性充沛"],
            "target_price_high": 3.5,
            "target_price_base": 3.3,
            "disproof_signals": ["AUM 骤降"],
        },
    )
    out = _run(EtfBullAdvocate(tmp_path)._execute({}))
    assert all(b.startswith("[V") for b in out["thesis_bullets"])


def test_bull_retry_placeholder_on_empty(monkeypatch, tmp_path):
    """When LLM returns 0 bullets both attempts → placeholder inserted."""
    from financial_analyst.agent.etf.bull_advocate import EtfBullAdvocate
    import financial_analyst.agent.etf.bull_advocate as m

    call_count = 0

    async def _chat_empty(self, *a, **k):
        nonlocal call_count
        call_count += 1
        return {"choices": [{"message": {"content": json.dumps(
            {"thesis_bullets": [], "target_price_high": 0.0, "target_price_base": 0.0, "disproof_signals": []}
        )}}]}

    monkeypatch.setattr(
        m,
        "LLMClient",
        type("X", (), {"for_agent": staticmethod(lambda n: type("C", (), {"chat": _chat_empty})())}),
        raising=False,
    )
    out = _run(EtfBullAdvocate(tmp_path)._execute({}))
    # After 2 attempts placeholder should be present
    assert len(out["thesis_bullets"]) >= 1
    assert call_count == 2  # retry attempted once


# ---------------------------------------------------------------------------
# Bear advocate
# ---------------------------------------------------------------------------

def test_bear_returns_valid_output(monkeypatch, tmp_path):
    from financial_analyst.agent.etf.bear_advocate import EtfBearAdvocate
    import financial_analyst.agent.etf.bear_advocate as m
    _patch(
        monkeypatch,
        m,
        {
            "thesis_bullets": ["[F1] 赛道拥挤已 price-in，估值透支", "[F3] 费率偏高拖累长期收益"],
            "target_price_low": 4.5,
            "downside_pct": -8.0,
        },
    )
    out = _run(EtfBearAdvocate(tmp_path)._execute({"etf-holdings-analyst": {"holdings_score": 1}}))
    assert len(out["thesis_bullets"]) >= 2
    assert all(b.startswith("[F") for b in out["thesis_bullets"])
    assert "target_price_low" in out
    assert "downside_pct" in out


def test_bear_f_anchor_prefix(monkeypatch, tmp_path):
    from financial_analyst.agent.etf.bear_advocate import EtfBearAdvocate
    import financial_analyst.agent.etf.bear_advocate as m
    _patch(
        monkeypatch,
        m,
        {
            "thesis_bullets": ["[F4] 持仓过度集中单票风险大", "[F6] AUM 持续萎缩清盘风险"],
            "target_price_low": 2.8,
            "downside_pct": -12.0,
        },
    )
    out = _run(EtfBearAdvocate(tmp_path)._execute({}))
    assert all(b.startswith("[F") for b in out["thesis_bullets"])


def test_bear_retry_placeholder_on_empty(monkeypatch, tmp_path):
    """When LLM returns 0 bullets both attempts → placeholder inserted."""
    from financial_analyst.agent.etf.bear_advocate import EtfBearAdvocate
    import financial_analyst.agent.etf.bear_advocate as m

    call_count = 0

    async def _chat_empty(self, *a, **k):
        nonlocal call_count
        call_count += 1
        return {"choices": [{"message": {"content": json.dumps(
            {"thesis_bullets": [], "target_price_low": 0.0, "downside_pct": 0.0}
        )}}]}

    monkeypatch.setattr(
        m,
        "LLMClient",
        type("X", (), {"for_agent": staticmethod(lambda n: type("C", (), {"chat": _chat_empty})())}),
        raising=False,
    )
    out = _run(EtfBearAdvocate(tmp_path)._execute({}))
    assert len(out["thesis_bullets"]) >= 1
    assert call_count == 2
