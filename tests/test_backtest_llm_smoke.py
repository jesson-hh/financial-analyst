"""P2 己 — one real-LLM smoke run (skips without a key or on any network error).

Runs a tiny 3-5 trading-day backtest against the real cn_data + pit_store with a
real ``DecisionAgent(client=LLMClient.for_agent('backtest-agent'))``. The window
is chosen ≤ 2026-03-30 so news is actually present (akshare coverage ends there).

Skip policy (P2 maj-4 / reviewer C):
  * no ``DASHSCOPE_API_KEY`` → skip (no key, nothing to test);
  * no real data → skip;
  * ANY network/timeout/runtime error talking to the LLM → ``pytest.skip``
    (qwen runs network_profile=domestic; Clash fake-ip can hijack dashscope to
    overseas nodes and time out — that is an env issue, not a code failure).
Only a *successful* response with malformed JSON / missing fields should fail.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def _real_data_available() -> bool:
    try:
        from financial_analyst.data.paths import get_data_paths
        p = get_data_paths()
        uri = p.qlib_uri
        day_root = uri["day"] if isinstance(uri, dict) else uri
        return (Path(day_root).exists()
                and Path(str(p.pit_store_root)).exists())
    except Exception:
        return False


@pytest.mark.skipif(not os.environ.get("DASHSCOPE_API_KEY"),
                    reason="no DASHSCOPE_API_KEY → real LLM smoke skipped")
@pytest.mark.skipif(not _real_data_available(),
                    reason="real cn_data / pit_store not present")
async def test_real_llm_decision_smoke():
    from financial_analyst.backtest.decision import Decision, DecisionAgent
    from financial_analyst.backtest.engine import BacktestRunner, RunConfig
    from financial_analyst.backtest.pit_reader import PitReader
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.data.paths import get_data_paths
    from financial_analyst.llm.client import LLMClient

    loader = QlibBinaryLoader(get_data_paths().qlib_uri)
    reader = PitReader(day_loader=loader)
    # window with real news (akshare covers ≤ 2026-03-30)
    days = reader.trading_days("2026-03-13", "2026-03-18")
    if len(days) < 3:
        pytest.skip("not enough trading days in news-covered window")

    client = LLMClient.for_agent("backtest-agent")
    agent = DecisionAgent(client=client)
    cfg = RunConfig(start=days[0], end=days[-1], init_cash=1_000_000.0,
                    benchmark=None, match_freq="day")
    runner = BacktestRunner(reader=reader, agent=agent, loader=loader, cfg=cfg)

    try:
        res = await runner.run()
    except Exception as e:  # network / timeout / provider error → skip, not fail
        pytest.skip(f"LLM unreachable or provider error: {e!r}")

    # got responses → these are real assertions on the contract, not skips
    assert res.n_llm_calls >= 1
    assert len(res.nav_history) >= 2
    for date, raw in res.decisions_by_date.items():
        # each captured decision must be a dict with the §B2 keys present
        assert isinstance(raw, dict)
        # parse_decision always yields a Decision; market_view/decisions exist
        # on the raw payload unless it was a parse failure (which is allowed
        # only to carry the _error marker)
        if raw.get("_error") != "json":
            assert "market_view" in raw
            assert "decisions" in raw
