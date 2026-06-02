"""P2 乙 — PitReader PIT-boundary tests (mirrors stocks ``get_visible_info``).

These are the most important P2 tests: they assert no future information leaks
into a decision. The boundary semantics are copied rule-for-rule from
``stocks/src/data/pit_store.py:get_visible_info`` (R1-R8 in the P2 design):

* R1  news truncation: ``pd.Timestamp(ts) > boundary_ts`` → drop (NOT lexical).
* R3  pre-open hides same-day intraday/post_close news (natural consequence of R1).
* R4  events: ``pd.Timestamp(ann_date) > pd.Timestamp(date)`` → drop (no as_of).
* R5  lookback accumulates [prev..date]; news sorted by ts, events by ann_date.
* R7  missing jsonl → []; non-trade-day → empty lists (market_eod_prev still computed).
* R8  market_eod_prev bound to prev_trade_date; P2 ships all-None placeholders.

All tests build a *temporary* pit_store on disk and a stub day-loader so they
never touch real data or the network. A real-data variant lives in
``test_backtest_engine.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from financial_analyst.backtest.pit_reader import (
    NewsItem,
    PitReader,
    PolicyItem,
    VisibleInfo,
)


# --------------------------------------------------------------------------
# Stub day-loader: a tiny calendar + one probe code so PitReader.data_end works
# without reading G:/stocks. The calendar is a plain list of pd.Timestamp like
# QlibBinaryLoader._load_calendar('day') returns.
# --------------------------------------------------------------------------
class _StubDayLoader:
    def __init__(self, days, probe_close_end="2026-03-20"):
        self._cal = [pd.Timestamp(d) for d in days]
        self._probe_end = pd.Timestamp(probe_close_end)

    def _load_calendar(self, freq="day"):
        if freq == "day":
            return self._cal
        raise ValueError(f"stub has no {freq} calendar")

    def fetch_quote(self, code, start, end, freq="day"):
        # data_end probe: return a frame whose max trade_date == probe_end
        rows = [d for d in self._cal if d <= self._probe_end]
        return pd.DataFrame({"trade_date": rows, "close": [10.0] * len(rows)})


_CAL = [
    "2026-03-13", "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19",
    "2026-03-20", "2026-03-23",
]


def _mk_reader(tmp_path: Path, days=_CAL) -> PitReader:
    return PitReader(store_root=tmp_path, day_loader=_StubDayLoader(days))


def _write_jsonl(tmp_path: Path, date: str, kind: str, rows):
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{kind}.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_policy(tmp_path: Path, date: str, rows):
    _write_jsonl(tmp_path, date, "policy", rows)


# ==========================================================================
# 乙1 — news: no future timestamp ever returned
# ==========================================================================
def test_news_no_future_ts(tmp_path):
    date = "2026-03-16"
    # mix of before-boundary and after-boundary ts on the SAME day
    _write_jsonl(tmp_path, date, "news", [
        {"ts": f"{date}T08:00:00", "date": date, "session": "pre_open",
         "code": None, "title": "early", "body": "b"},
        {"ts": f"{date}T09:24:59", "date": date, "session": "pre_open",
         "code": None, "title": "just-before", "body": "b"},
        {"ts": f"{date}T09:25:01", "date": date, "session": "pre_open",
         "code": None, "title": "just-after", "body": "b"},
        {"ts": f"{date}T14:30:00", "date": date, "session": "intraday",
         "code": None, "title": "intraday", "body": "b"},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25")
    assert isinstance(vi, VisibleInfo)
    boundary = pd.Timestamp(f"{date} 09:25:00")
    assert all(pd.Timestamp(n.ts) <= boundary for n in vi.news)
    titles = {n.title for n in vi.news}
    assert "just-before" in titles and "early" in titles
    assert "just-after" not in titles and "intraday" not in titles


# ==========================================================================
# 乙2 — pre-open (09:25) hides same-day intraday / post_close news
# ==========================================================================
def test_pre_open_hides_intraday(tmp_path):
    date = "2026-03-17"
    _write_jsonl(tmp_path, date, "news", [
        {"ts": f"{date}T09:10:00", "date": date, "session": "pre_open",
         "code": None, "title": "pre", "body": "b"},
        {"ts": f"{date}T10:30:00", "date": date, "session": "intraday",
         "code": None, "title": "mid", "body": "b"},
        {"ts": f"{date}T15:10:00", "date": date, "session": "post_close",
         "code": None, "title": "post", "body": "b"},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25")
    # explicit design assertion: no same-day intraday/post_close survives
    bad = [n for n in vi.news
           if n.date == date and n.session in ("intraday", "post_close")]
    assert bad == []
    assert {n.title for n in vi.news} == {"pre"}


# ==========================================================================
# 乙3 — events: ann_date > date dropped (does not depend on as_of)
# ==========================================================================
def test_events_ann_date_boundary(tmp_path):
    date = "2026-03-18"
    nxt = "2026-03-19"
    _write_jsonl(tmp_path, date, "events", [
        {"code": "SH600000", "type": "block_trade", "summary": "today ok",
         "ann_date": date, "session": "pre_open", "v": 1},
    ])
    # a future-dated event physically placed in a later dir; with lookback it
    # would not be in cand_days anyway, but also assert the ann_date guard
    _write_jsonl(tmp_path, nxt, "events", [
        {"code": "SH600000", "type": "block_trade", "summary": "future",
         "ann_date": nxt, "session": "pre_open", "v": 1},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25", lookback_days=1)
    assert all(pd.Timestamp(e.ann_date) <= pd.Timestamp(date) for e in vi.events)
    assert {e.summary for e in vi.events} == {"today ok"}


# ==========================================================================
# 乙4 — market_eod_prev: prev_trade_date strict-< and skips holidays; all-None
# ==========================================================================
def test_market_eod_prev_all_none(tmp_path):
    date = "2026-03-23"  # prev trading day is 2026-03-20 (weekend gap 21/22)
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25")
    meb = vi.market_eod_prev
    assert meb is not None
    assert meb["prev_trade_date"] == "2026-03-20"
    assert pd.Timestamp(meb["prev_trade_date"]) < pd.Timestamp(date)
    # P2 placeholder: width fields are all None (not csiall-mirror values)
    assert meb["pct_up_5d"] is None
    assert meb["median_ret_5d"] is None
    assert meb["median_ret_20d"] is None


# ==========================================================================
# 乙5 — lookback accumulation + robustness (missing files / non-trade-day)
# ==========================================================================
def test_lookback_and_robust(tmp_path):
    prev, date = "2026-03-18", "2026-03-19"
    # prev day has a news item; date day has events file only (news missing)
    _write_jsonl(tmp_path, prev, "news", [
        {"ts": f"{prev}T09:00:00", "date": prev, "session": "pre_open",
         "code": None, "title": "from-prev", "body": "b"},
    ])
    _write_jsonl(tmp_path, date, "events", [
        {"code": "SH600000", "type": "x", "summary": "ev",
         "ann_date": date, "session": "pre_open", "v": 1},
    ])
    r = _mk_reader(tmp_path)
    # lookback_days=1 → [prev, date]; prev-day news accumulates into the window
    vi = r.get_visible_info(date, codes=None, as_of="09:25", lookback_days=1)
    assert "from-prev" in {n.title for n in vi.news}
    # missing news.jsonl on `date` does not raise
    assert isinstance(vi.news, list)

    # non-trade-day → empty lists but market_eod_prev still computed
    non = "2026-03-21"  # Saturday, not in _CAL
    vi2 = r.get_visible_info(non, codes=None, as_of="09:25")
    assert vi2.news == [] and vi2.events == []
    assert vi2.market_eod_prev is not None
    assert vi2.market_eod_prev["prev_trade_date"] == "2026-03-20"


# ==========================================================================
# extra — codes filter keeps None-coded (market-wide) news, filters events
# ==========================================================================
def test_codes_filter_keeps_market_news(tmp_path):
    date = "2026-03-16"
    _write_jsonl(tmp_path, date, "news", [
        {"ts": f"{date}T09:00:00", "date": date, "session": "pre_open",
         "code": None, "title": "market-wide", "body": "b"},
        {"ts": f"{date}T09:01:00", "date": date, "session": "pre_open",
         "code": "SH600519", "title": "kept", "body": "b"},
        {"ts": f"{date}T09:02:00", "date": date, "session": "pre_open",
         "code": "SZ000001", "title": "filtered", "body": "b"},
    ])
    _write_jsonl(tmp_path, date, "events", [
        {"code": "SH600519", "type": "x", "summary": "kept-ev",
         "ann_date": date, "session": "pre_open", "v": 1},
        {"code": "SZ000001", "type": "x", "summary": "drop-ev",
         "ann_date": date, "session": "pre_open", "v": 1},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=["SH600519"], as_of="09:25")
    titles = {n.title for n in vi.news}
    assert "market-wide" in titles  # code=None always kept
    assert "kept" in titles
    assert "filtered" not in titles
    assert {e.summary for e in vi.events} == {"kept-ev"}  # events strict on code


# ==========================================================================
# extra — unknown jsonl fields land in NewsItem.extra (forward-compat, v=1)
# ==========================================================================
def test_unknown_fields_preserved_in_extra(tmp_path):
    date = "2026-03-16"
    _write_jsonl(tmp_path, date, "news", [
        {"ts": f"{date}T09:00:00", "date": date, "session": "pre_open",
         "code": None, "title": "t", "body": "b",
         "sentiment_score": 0.7, "brand_new_field": "x"},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25")
    assert len(vi.news) == 1
    n = vi.news[0]
    assert isinstance(n, NewsItem)
    assert n.extra.get("sentiment_score") == 0.7
    assert n.extra.get("brand_new_field") == "x"


# ==========================================================================
# extra — malformed jsonl line is skipped, not raised
# ==========================================================================
def test_malformed_line_skipped(tmp_path):
    date = "2026-03-16"
    d = tmp_path / date
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "news.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": f"{date}T09:00:00", "date": date,
                            "session": "pre_open", "code": None,
                            "title": "good", "body": "b"}) + "\n")
        f.write("{ this is not valid json\n")
        f.write("\n")  # blank line
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25")
    assert {n.title for n in vi.news} == {"good"}


# ==========================================================================
# policy — same ts<=boundary judge as news (mirrors stocks T_POL1)
# ==========================================================================
def test_policy_no_future_ts(tmp_path):
    date = "2026-03-16"
    _write_policy(tmp_path, date, [
        {"pub_date": date, "ts": f"{date}T08:20:00", "session": "pre_open",
         "level": "macro_news", "title": "early", "summary": "s", "code": None, "v": 1},
        {"pub_date": date, "ts": f"{date}T14:00:00", "session": "intraday",
         "level": "macro_news", "title": "mid", "summary": "s", "code": None, "v": 1},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25", include=("policy",))
    assert all(pd.Timestamp(p.ts) <= pd.Timestamp(f"{date} 09:25:00") for p in vi.policy)
    assert {p.title for p in vi.policy} == {"early"}        # mid (14:00) 被剪
    assert all(isinstance(p, PolicyItem) for p in vi.policy)


# ==========================================================================
# policy — code=null gov policy kept on any codes (news-style, not events-style)
# ==========================================================================
def test_policy_codes_keeps_null_gov(tmp_path):
    date = "2026-03-16"
    _write_policy(tmp_path, date, [
        {"pub_date": date, "ts": f"{date}T08:00:00", "session": "pre_open",
         "level": "gov", "title": "gov-wide", "summary": "s", "code": None, "v": 1},
        {"pub_date": date, "ts": f"{date}T08:01:00", "session": "pre_open",
         "level": "macro_news", "title": "stock-X", "summary": "s", "code": "SH600519", "v": 1},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=["SZ000001"], as_of="09:25", include=("policy",))
    titles = {p.title for p in vi.policy}
    assert "gov-wide" in titles          # null kept (news-style)
    assert "stock-X" not in titles       # single-code filtered (SH600519 ∉ {SZ000001})


# ==========================================================================
# policy — default include now carries policy (regression guard for the
# blocker: if include default drops policy, fa-side policy is silently empty)
# ==========================================================================
def test_policy_in_default_include(tmp_path):
    date = "2026-03-16"
    _write_policy(tmp_path, date, [
        {"pub_date": date, "ts": f"{date}T08:00:00", "session": "pre_open",
         "level": "gov", "title": "gov-default", "summary": "s", "code": None, "v": 1},
    ])
    r = _mk_reader(tmp_path)
    vi = r.get_visible_info(date, codes=None, as_of="09:25")  # no explicit include
    assert {p.title for p in vi.policy} == {"gov-default"}
