# tests/integration/test_smoke_deep_dive.py
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_smoke_tier1_data_layer_wires(tmp_path, monkeypatch):
    """Smoke test: verify Tier 1 data fetchers integrate with mocked Tushare + LLM."""
    from financial_analyst.tui import _ensure_registered
    from financial_analyst.swarm import load_preset
    from financial_analyst.agent.orchestrator import Orchestrator

    _ensure_registered()
    mem_root = tmp_path / "memories"
    mem_root.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    import numpy as np
    rng = np.random.default_rng(42)
    prices = 10.0 + np.cumsum(rng.normal(0, 0.2, 80))
    prices = np.abs(prices) + 8.0  # keep positive
    vols = 1e6 * (1 + rng.uniform(0, 1, 80))
    fake_quote = pd.DataFrame({
        "trade_date": pd.date_range("2026-02-01", periods=80, freq="B"),
        "open": prices * rng.uniform(0.98, 1.02, 80),
        "high": prices * rng.uniform(1.01, 1.05, 80),
        "low": prices * rng.uniform(0.95, 0.99, 80),
        "close": prices,
        "vol": vols,
        "amount": prices * vols,
    })
    fake_db = pd.DataFrame({
        "trade_date": pd.date_range("2026-02-01", periods=80, freq="B"),
        "pe_ttm": [25.0] * 80,
        "pb": [3.0] * 80,
        "ps_ttm": [2.0] * 80,
        "dv_ttm": [1.5] * 80,
        "total_mv": [80_0000.0] * 80,
        "circ_mv": [50_0000.0] * 80,
        "turnover_rate": [3.5] * 80,
    })

    # Mock TushareLoader at construction — patch both quote-fetcher and factor-computer modules
    mock_loader = MagicMock()
    mock_loader.fetch_quote.return_value = fake_quote
    mock_loader.fetch_daily_basic.return_value = fake_db

    with patch("financial_analyst.agent.tier1.quote_fetcher.TushareLoader", return_value=mock_loader), \
         patch("financial_analyst.agent.tier1.factor_computer.TushareLoader", return_value=mock_loader), \
         patch("financial_analyst.models.lgb_momentum.TushareLoader", return_value=mock_loader):

        # Mock LiteLLM for any Tier 2/3 agent that gets called
        stub_response = MagicMock()
        stub_response.__getitem__ = lambda self, key: {
            "choices": [{"message": {"content": (
                '{"valuation_score": 1, "mv_tier": "large", '
                '"dimension_detail": {}, "red_flags": [], "bull_points": [], "bear_points": []}'
            )}}]
        }[key]

        # Build a more realistic stub that works with dict-style access
        stub_msg = MagicMock()
        stub_msg.content = (
            '{"valuation_score": 1, "mv_tier": "large", '
            '"dimension_detail": {}, "red_flags": [], "bull_points": [], "bear_points": []}'
        )
        stub_choice = MagicMock()
        stub_choice.message = stub_msg
        stub_acompletion_result = MagicMock()
        stub_acompletion_result.choices = [stub_choice]

        with patch("litellm.acompletion", AsyncMock(return_value=stub_acompletion_result)):
            nodes = load_preset("stock-deep-dive", memory_root=mem_root)
            orch = Orchestrator(nodes)
            results = await orch.run({
                "code": "SH600519",
                "asof_date": "2026-05-17",
                "out_dir": str(out_dir),
            })

    # Tier 1 trusted fetchers should succeed with mocked loader
    assert results["quote-fetcher"].ok, (
        f"quote-fetcher failed: {results['quote-fetcher'].error}"
    )
    assert results["factor-computer"].ok, (
        f"factor-computer failed: {results['factor-computer'].error}"
    )
    # model-predictor uses ModelRegistry which has lgb_momentum registered
    assert results["model-predictor"].ok, (
        f"model-predictor failed: {results['model-predictor'].error}"
    )
    # news-reader and f10-reader return empty when no drop-zone dirs — should succeed
    assert results["news-reader"].ok, (
        f"news-reader failed: {results['news-reader'].error}"
    )
    assert results["f10-reader"].ok, (
        f"f10-reader failed: {results['f10-reader'].error}"
    )
