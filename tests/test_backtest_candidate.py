"""P2 丙 — candidate pool tests (§246 degraded: holdings ∪ watchlist ∪ rev_20).

Asserts the PIT discipline of the candidate stage:
* the ONLY market data source is ``reader.fetch_quote_leq_prev(end=prev)`` —
  never T-day data (丙1);
* rev_20 is computed off ≤T-1 close with a *trading-day* lookback so a stock
  with exactly 21 points is not dropped (丙1);
* holdings and watchlist are unioned, the SH999999 sentinel filtered, and
  ``universe_source`` labels each code (丙2).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from financial_analyst.backtest.candidate import (
    CandidateConfig,
    CandidateResult,
    select_candidates,
)


# --------------------------------------------------------------------------
# Stub reader exposing exactly the surface candidate.py uses:
#   prev_trade_date(date), fetch_quote_leq_prev(code, n_days_back, freq, as_of_date)
# Records every fetch call so 丙1 can assert no T-day read happened.
# --------------------------------------------------------------------------
class _StubReader:
    def __init__(self, cal, close_by_code):
        self._cal = [str(pd.Timestamp(d).date()) for d in cal]
        self._close = close_by_code  # code -> dict{date_str: close}
        self.fetch_calls = []  # list of (code, as_of_date, end_used)

    def prev_trade_date(self, date):
        earlier = [d for d in self._cal if d < date]
        return earlier[-1] if earlier else None

    def fetch_quote_leq_prev(self, code, n_days_back=30, freq="day", as_of_date=None):
        prev = self.prev_trade_date(as_of_date)
        self.fetch_calls.append((code, as_of_date, prev))
        series = self._close.get(code, {})
        if prev is None:
            return pd.DataFrame(columns=["trade_date", "close"])
        # all dates <= prev, take last n_days_back trading days
        days = [d for d in self._cal if d <= prev][-n_days_back:]
        rows = [(d, series[d]) for d in days if d in series]
        return pd.DataFrame(rows, columns=["trade_date", "close"])


# 25 trading days so a 30-trading-day lookback yields >=21 points cleanly.
_CAL = [str(d.date()) for d in pd.bdate_range("2026-02-02", periods=25)]
_DATE = str(pd.bdate_range("2026-02-02", periods=26)[-1].date())  # day after last cal day
_PREV = _CAL[-1]


def _ramp(start, step):
    """A monotone close series across _CAL (deterministic rev_20)."""
    return {d: start + i * step for i, d in enumerate(_CAL)}


# ==========================================================================
# 丙1 — candidate stage only reads ≤T-1; rev_20 deterministic; 21-pt edge kept
# ==========================================================================
def test_candidate_only_uses_leq_prev(tmp_path, monkeypatch):
    # 5 toy codes with distinct monotone ramps → distinct 20-day reversal
    closes = {
        "SH600001": _ramp(10.0, 0.0),    # flat → rev20 == 0
        "SH600002": _ramp(10.0, -0.10),  # falling hard → most negative rev20
        "SH600003": _ramp(10.0, 0.10),   # rising hard → most positive rev20
        "SH600004": _ramp(10.0, -0.05),
        "SH600005": _ramp(10.0, 0.05),
    }
    reader = _StubReader(_CAL, closes)
    # empty watchlist so the universe is purely the holdings we pass below
    wl = tmp_path / "watchlist.parquet"
    pd.DataFrame({"code": list(closes), "source_file": ["x"] * 5,
                  "position": [0] * 5, "sync_time": ["t"] * 5}).to_parquet(wl)
    cfg = CandidateConfig(topn=2, watchlist_path=wl, rev20_pick="low",
                          include_holdings=False)

    res = select_candidates(_DATE, holdings=[], reader=reader, cfg=cfg)
    assert isinstance(res, CandidateResult)
    # every fetch used end == prev_trade_date(date), never date itself
    assert reader.fetch_calls, "candidate stage made no quote reads"
    for code, as_of_date, end_used in reader.fetch_calls:
        assert as_of_date == _DATE
        assert end_used == _PREV
        assert end_used < _DATE  # strictly ≤ T-1
    assert res.asof_prev == _PREV

    # manual rev_20 check: close[-1]/close[-21]-1 on the ≤prev window
    def rev20(code):
        s = closes[code]
        days = [d for d in _CAL if d <= _PREV]
        return s[days[-1]] / s[days[-21]] - 1.0

    raw = {c: rev20(c) for c in closes}
    # nsmallest(2) = the two most-negative reversal → falling stocks
    expect_top = set(pd.Series(raw).nsmallest(2).index)
    assert expect_top == {"SH600002", "SH600004"}
    assert set(res.codes) >= expect_top
    # rev20_rank is a 0..1 pct rank, lowest reversal → lowest rank
    assert res.rev20_rank["SH600002"] < res.rev20_rank["SH600003"]
    # edge: all 5 codes have exactly len(window)>=21 points → none dropped
    assert set(res.rev20_rank) == set(closes)


# ==========================================================================
# 丙2 — holdings ∪ watchlist union, sentinel filtered, source labelled
# ==========================================================================
def test_candidate_includes_holdings_watchlist(tmp_path):
    closes = {
        "SH600002": _ramp(10.0, -0.10),
        "SH600003": _ramp(10.0, 0.10),
        "SH600600": _ramp(10.0, -0.02),  # a watchlist name
    }
    reader = _StubReader(_CAL, closes)
    wl = tmp_path / "watchlist.parquet"
    # row0 is the SH999999 sentinel exactly as in the real watchlist.parquet
    pd.DataFrame({
        "code": ["SH999999", "SH600600", "SH600003"],
        "source_file": ["zxg.blk"] * 3,
        "position": [0, 0, 0],
        "sync_time": ["2026-05-30 14:14:11"] * 3,
    }).to_parquet(wl)
    cfg = CandidateConfig(topn=1, watchlist_path=wl, rev20_pick="low")

    holdings = ["SH600002"]  # a held position not in the watchlist
    res = select_candidates(_DATE, holdings=holdings, reader=reader, cfg=cfg)

    # sentinel never appears
    assert "SH999999" not in res.codes
    # every holding is present
    assert "SH600002" in res.codes
    # watchlist names present
    assert "SH600600" in res.codes and "SH600003" in res.codes
    # rev20 nsmallest(1) over the base universe → SH600002 (steepest fall)
    assert res.universe_source["SH600002"] == "holding"
    assert res.universe_source["SH600600"] == "watchlist"
    # holdings come first in the ordered codes list
    assert res.codes[0] == "SH600002"


# ==========================================================================
# extra — default watchlist_path resolves to get_data_paths().parquet_root
# (only structural: ensure no crash when path omitted & file exists or not)
# ==========================================================================
def test_candidate_handles_missing_watchlist(tmp_path):
    closes = {"SH600002": _ramp(10.0, -0.10)}
    reader = _StubReader(_CAL, closes)
    missing = tmp_path / "nope.parquet"
    cfg = CandidateConfig(topn=1, watchlist_path=missing,
                          include_watchlist=True, rev20_pick="low")
    res = select_candidates(_DATE, holdings=["SH600002"], reader=reader, cfg=cfg)
    # missing watchlist file → just holdings, no crash
    assert res.codes == ["SH600002"]
    assert res.universe_source["SH600002"] == "holding"
