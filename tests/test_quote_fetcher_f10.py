# tests/test_quote_fetcher_f10.py
import sys, pathlib, asyncio
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
import pandas as pd
from financial_analyst.agent.tier1 import quote_fetcher as qf
from financial_analyst.data import f10_corpus as fc


class FakeLoader:
    def fetch_quote(self, code, start, end):
        idx = pd.date_range("2026-05-01", periods=70, freq="D")
        return pd.DataFrame({"close": [6.0] * 70, "vol": [1e6] * 70}, index=idx)

    def fetch_daily_basic(self, code, start, end):
        return pd.DataFrame()   # 空 -> 触发 F10 兜底


def test_quote_fetcher_uses_f10_when_daily_basic_empty(tmp_path, monkeypatch):
    fixt = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10"
    monkeypatch.setattr(fc, "CORPUS_ROOT", fixt)
    agent = qf.QuoteFetcher(memory_root=tmp_path, loader=FakeLoader())
    out = asyncio.run(agent._execute({"code": "SZ000630", "asof_date": "2026-06-01"}))
    # 总股本 13409470000 股 × 6.0 元 / 1e8 = 804.5682 亿
    # (注:计划注释 "8045.7" 系 10x 笔误;134.0947亿股=1.34e10,×6.0/1e8=804.57亿)
    assert out["mv_yi"] is not None and round(out["mv_yi"], 1) == 804.6
    assert out["pb"] is not None and round(out["pb"], 3) == round(6.0 / 2.7954, 3)
    # f10_valuation 透传,供下游真营收/净利/ROE
    assert out["f10_valuation"] is not None
    assert out["f10_valuation"]["total_shares"] == 13409470000.0
