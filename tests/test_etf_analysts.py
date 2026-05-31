"""Tests for ETF tier-2 LLM analysts: holdings / technical / flow / valuation."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path


def _run(c):
    return asyncio.run(c) if asyncio.iscoroutine(c) else c


# ---------------------------------------------------------------------------
# Shared LLM mock factory
# ---------------------------------------------------------------------------

def _make_mock_client(payload: dict):
    """Return a fake LLMClient class whose .chat() coroutine yields payload."""
    payload_str = json.dumps(payload)

    class _Resp:
        def __init__(self):
            self.choices = [type("C", (), {"message": type("M", (), {"content": payload_str})()})()]

        def __getitem__(self, key):
            # Support dict-style access response["choices"][0]["message"]["content"]
            if key == "choices":
                return [{"message": {"content": payload_str}}]
            raise KeyError(key)

    class _Client:
        async def chat(self, *a, **kw):
            return _Resp()

    class _LLMClient:
        @staticmethod
        def for_agent(name):
            return _Client()

    return _LLMClient


# ---------------------------------------------------------------------------
# Holdings analyst
# ---------------------------------------------------------------------------

def test_holdings_analyst(monkeypatch, tmp_path):
    payload = {
        "holdings_score": 1,
        "bull_points": ["集中度合理，前十权重52%"],
        "bear_points": ["单票偏高，最大权重9.5%"],
        "top_holding_weight": 9.5,
        "sector_concentration_hhi": 0.12,
        "index_methodology_note": "宽基等权",
    }
    import financial_analyst.agent.etf.holdings_analyst as m
    monkeypatch.setattr(m, "LLMClient", _make_mock_client(payload), raising=False)
    from financial_analyst.agent.etf.holdings_analyst import EtfHoldingsAnalyst
    a = EtfHoldingsAnalyst(memory_root=tmp_path)
    out = _run(a._execute({
        "etf-metrics-fetcher": {
            "holdings": {"end_date": "20260331", "holdings": [{"symbol": "600519.SH", "ratio": 9.5}]},
        }
    }))
    assert -2 <= out["holdings_score"] <= 2
    assert "bull_points" in out
    assert "bear_points" in out
    assert "sector_concentration_hhi" in out
    assert "top_holding_weight" in out
    assert "index_methodology_note" in out


# ---------------------------------------------------------------------------
# Technical analyst
# ---------------------------------------------------------------------------

def test_etf_technical_analyst(monkeypatch, tmp_path):
    payload = {
        "technical_score": -1,
        "bull_points": ["MA5仍在MA20之上"],
        "bear_points": ["RSI近70，超买风险"],
        "ma_state": "bullish",
        "rsi_state": "overbought",
        "breakout_signal": None,
    }
    import financial_analyst.agent.etf.technical_analyst as m
    monkeypatch.setattr(m, "LLMClient", _make_mock_client(payload), raising=False)
    from financial_analyst.agent.etf.technical_analyst import EtfTechnicalAnalyst
    a = EtfTechnicalAnalyst(memory_root=tmp_path)
    out = _run(a._execute({
        "etf-quote-fetcher": {
            "close": 4.92, "ret_5d": 0.02, "ret_20d": 0.05,
            "ma5": 4.88, "ma20": 4.75, "ma60": 4.60,
            "volatility": 0.12, "volume_ratio": 1.1,
        }
    }))
    assert -2 <= out["technical_score"] <= 2
    assert "bull_points" in out
    assert "bear_points" in out
    assert "ma_state" in out
    assert "rsi_state" in out
    assert "breakout_signal" in out


# ---------------------------------------------------------------------------
# Flow analyst
# ---------------------------------------------------------------------------

def test_etf_flow_analyst(monkeypatch, tmp_path):
    payload = {
        "flow_score": 2,
        "bull_points": ["近5日持续净申购", "AUM创阶段新高"],
        "bear_points": ["机构持仓集中，散户有摊薄风险"],
        "flow_regime": "net_inflow",
        "aum_trend": "rising",
        "liquidity_note": "日均换手率0.8%，流动性充足",
    }
    import financial_analyst.agent.etf.flow_analyst as m
    monkeypatch.setattr(m, "LLMClient", _make_mock_client(payload), raising=False)
    from financial_analyst.agent.etf.flow_analyst import EtfFlowAnalyst
    a = EtfFlowAnalyst(memory_root=tmp_path)
    out = _run(a._execute({
        "etf-metrics-fetcher": {
            "flow": {"latest_share_change": 5000.0, "aum_latest": 1.5e7, "aum_unit": "wan_yuan"},
            "nav": {"unit_nav": 4.92},
        }
    }))
    assert -2 <= out["flow_score"] <= 2
    assert "bull_points" in out
    assert "bear_points" in out
    assert "flow_regime" in out
    assert "aum_trend" in out
    assert "liquidity_note" in out


# ---------------------------------------------------------------------------
# Valuation analyst
# ---------------------------------------------------------------------------

def test_etf_valuation_analyst(monkeypatch, tmp_path):
    payload = {
        "valuation_score": 0,
        "bull_points": ["折溢价接近0，定价公允"],
        "bear_points": ["年化费率0.5%，略高于同类"],
        "premium_discount_state": "at_par",
        "tracking_error_level": "low",
        "fee_drag_note": "年化总费率0.5%，管理费0.15%+托管费0.05%",
    }
    import financial_analyst.agent.etf.valuation_analyst as m
    monkeypatch.setattr(m, "LLMClient", _make_mock_client(payload), raising=False)
    from financial_analyst.agent.etf.valuation_analyst import EtfValuationAnalyst
    a = EtfValuationAnalyst(memory_root=tmp_path)
    out = _run(a._execute({
        "etf-metrics-fetcher": {
            "premium_discount": {"realtime_premium_discount_pct": 0.02},
            "tracking_error": {"tracking_error_annualized": 0.003, "window": 60},
        },
        "etf-quote-fetcher": {
            "total_fee": 0.5, "m_fee": 0.15, "c_fee": 0.05,
        },
    }))
    assert -2 <= out["valuation_score"] <= 2
    assert "bull_points" in out
    assert "bear_points" in out
    assert "premium_discount_state" in out
    assert "tracking_error_level" in out
    assert "fee_drag_note" in out
