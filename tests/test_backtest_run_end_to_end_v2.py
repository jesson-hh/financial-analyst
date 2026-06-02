"""buddy.backtest_run.run_backtest 接住 P2 新字段: pool/hold_days/take/stop"""
import pytest
import asyncio
from financial_analyst.buddy.server import BacktestRunReq
from financial_analyst.buddy.backtest_run import run_backtest


# 标 slow + 需要真 data layer (csi_fast 成分股), 在缺数据环境会 skip
pytestmark = pytest.mark.slow


def _skip_if_data_layer_missing(window_start: str):
    """Skip if csi_fast unresolvable OR if data_end < window_start (CI conftest
    rewires PitReader to a tmp fake calendar with only 2026-05-01)."""
    try:
        from financial_analyst.data.universe import resolve_universe_codes
        from financial_analyst.backtest.pit_reader import PitReader
        import pandas as pd
        if not resolve_universe_codes("csi_fast"):
            pytest.skip("csi_fast 池子未解析 (缺 universes/csi_fast.txt 或 index_constituents.parquet)")
        try:
            de = PitReader().data_end()
            if pd.Timestamp(window_start) > pd.Timestamp(de):
                pytest.skip(f"data_end={de.date()} < window_start={window_start} (CI fake data?)")
        except pytest.skip.Exception:
            raise
        except Exception:
            pass  # PitReader 没起来就让真测自己崩
    except pytest.skip.Exception:
        raise
    except Exception as e:
        pytest.skip(f"data layer 缺: {e}")


@pytest.mark.asyncio
async def test_run_backtest_threads_pool_to_candidate_config():
    """req.pool='csi_fast' → CandidateConfig.pool='csi_fast' → 池子模式跑"""
    _skip_if_data_layer_missing("2026-05-23")
    req = BacktestRunReq(
        start="2026-05-23", end="2026-05-30",
        pool="csi_fast", hold_days=3, mode="mock", candidate_topn=5,
    )
    result = await run_backtest(req)
    trades = result.get("trades", [])
    actions = [t["action"] for t in trades]
    assert "buy" in actions, f"窗口内应有 buy, trades={trades}"


@pytest.mark.asyncio
async def test_run_backtest_threads_hold_days_to_mock_agent():
    """req.hold_days=5 → _MockAgent(hold_days=5) — 不报错就 OK"""
    _skip_if_data_layer_missing("2026-05-19")
    req = BacktestRunReq(
        start="2026-05-19", end="2026-05-30",
        pool="csi_fast", hold_days=5, mode="mock", candidate_topn=3,
    )
    result = await run_backtest(req)
    assert result.get("status") != "error", result.get("error")
