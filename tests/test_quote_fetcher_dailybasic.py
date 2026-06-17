# tests/test_quote_fetcher_dailybasic.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd
from financial_analyst.agent.tier1 import quote_fetcher as qf


class FakeLoader:
    def fetch_quote(self, code, start, end):
        idx = pd.date_range("2026-03-20", periods=80, freq="D")
        return pd.DataFrame({"close": [6.25] * 80, "vol": [1e6] * 80}, index=idx)

    def fetch_daily_basic(self, code, start, end):
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        if s <= pd.Timestamp("2026-06-09") <= e:
            return pd.DataFrame(
                [{
                    "pe_ttm": 31.87, "pb": 2.28, "ps_ttm": None, "dv_ttm": None,
                    "total_mv": 8380900.0, "circ_mv": 6964400.0, "turnover_rate": 3.99,
                }],
                index=[pd.Timestamp("2026-06-09")],
            )
        return pd.DataFrame()


def test_widened_window_recovers_real_pe(tmp_path):
    agent = qf.QuoteFetcher(memory_root=tmp_path, loader=FakeLoader())
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-15"}))
    assert out["pe"] == 31.87 and out["pb"] == 2.28
    assert round(out["mv_yi"], 2) == 838.09        # total_mv/10000
    assert out.get("f10_valuation") is None        # 没走 F10 兜底
