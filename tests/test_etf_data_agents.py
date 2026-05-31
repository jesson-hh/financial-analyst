"""Tests for ETF tier-1 data agents: EtfQuoteFetcher + EtfMetricsFetcher."""
import asyncio
import pandas as pd
from financial_analyst.agent.etf.quote_fetcher import EtfQuoteFetcher
from financial_analyst.agent.etf.metrics_fetcher import EtfMetricsFetcher


class _FakeLoader:
    def fetch_etf_quote(self, *a, **k):
        return pd.DataFrame({
            "trade_date": ["2026-05-27", "2026-05-28", "2026-05-29"],
            "open": [4.9, 4.91, 4.92],
            "high": [5.0, 5.0, 4.95],
            "low": [4.8, 4.85, 4.9],
            "close": [4.90, 4.91, 4.92],
            "vol": [100, 110, 120],
            "amount": [49000, 54000, 59000],
        })

    def fetch_etf_meta(self, c):
        return {
            "name": "300ETF",
            "m_fee": 0.15,
            "c_fee": 0.05,
            "total_fee": 0.2,
            "benchmark": "沪深300",
            "index_code": "000300.SH",
            "fund_type": "ETF",
        }

    def fetch_etf_premium_discount(self, c):
        return {"realtime_premium_discount_pct": -0.1}

    def fetch_etf_nav(self, c, *a, **k):
        return pd.DataFrame({"nav_date": ["2026-05-29"], "unit_nav": [4.91]})

    def fetch_etf_flow(self, c, *a, **k):
        return {"latest_share_change": -1260.0, "aum_latest": 1.37e7, "aum_unit": "wan_yuan"}

    def fetch_tracking_error(self, c, *a, **k):
        return {"tracking_error_annualized": 0.0022, "window": 60}

    def fetch_etf_holdings(self, c, *a, **k):
        return {"end_date": "20260331", "holdings": [{"symbol": "600519.SH", "ratio": 9.0}]}


def _run(coro_or_val):
    return asyncio.run(coro_or_val) if asyncio.iscoroutine(coro_or_val) else coro_or_val


def test_quote_fetcher(tmp_path):
    a = EtfQuoteFetcher(memory_root=tmp_path, loader=_FakeLoader())
    out = _run(a._execute({"code": "SH510300", "asof_date": "2026-05-29"}))
    assert out["name"] == "300ETF"
    assert out["total_fee"] == 0.2
    assert out["close"] == 4.92


def test_quote_fetcher_returns_computed(tmp_path):
    a = EtfQuoteFetcher(memory_root=tmp_path, loader=_FakeLoader())
    out = _run(a._execute({"code": "SH510300", "asof_date": "2026-05-29"}))
    # ma5 should be the mean of last 3 rows (only 3 rows in fake data)
    assert out["ma5"] is not None
    assert out["close"] == 4.92


def test_quote_fetcher_output_schema_valid(tmp_path):
    """run() wraps _execute and validates via OUTPUT_SCHEMA."""
    a = EtfQuoteFetcher(memory_root=tmp_path, loader=_FakeLoader())
    result = _run(a.run({"code": "SH510300", "asof_date": "2026-05-29"}))
    assert result.ok, result.error
    assert result.output.name == "300ETF"


def test_metrics_fetcher(tmp_path):
    a = EtfMetricsFetcher(memory_root=tmp_path, loader=_FakeLoader())
    out = _run(a._execute({"code": "SH510300", "asof_date": "2026-05-29"}))
    assert out["premium_discount"]["realtime_premium_discount_pct"] == -0.1
    assert out["tracking_error"]["tracking_error_annualized"] == 0.0022
    assert out["holdings"]["end_date"] == "20260331"


def test_metrics_fetcher_nav(tmp_path):
    a = EtfMetricsFetcher(memory_root=tmp_path, loader=_FakeLoader())
    out = _run(a._execute({"code": "SH510300", "asof_date": "2026-05-29"}))
    assert out["nav"]["unit_nav"] == 4.91


def test_metrics_fetcher_output_schema_valid(tmp_path):
    """run() wraps _execute and validates via OUTPUT_SCHEMA."""
    a = EtfMetricsFetcher(memory_root=tmp_path, loader=_FakeLoader())
    result = _run(a.run({"code": "SH510300", "asof_date": "2026-05-29"}))
    assert result.ok, result.error
