"""PIT (point-in-time) data gate for the P2 decision backtest — zero stocks import.

The DecisionAgent's *only* data source is this reader. It mirrors, rule for rule,
the boundary semantics of ``stocks/src/data/pit_store.py:get_visible_info`` but
parses the jsonl with fa-local dataclasses (never imports the stocks package or
its ``EventItem``). See the P2 design §1 (R1-R8) for the rule table.

Two hardening points beyond a literal mirror (see §1.6 / §1.4):

* ``data_end`` — the day calendar is padded with future, dataless dates
  (``day.txt`` runs to 2026-12-31) while real bars stop at 2026-05-29. Iterating
  the padded tail would silently freeze NAV on empty bars. ``data_end`` probes a
  reference code's last non-NaN close and ``trading_days`` caps to it.
* ``market_eod_prev`` — fa has no neutral csiall universe API, so the market
  breadth fields ship as **all-None placeholders** (field names identical to the
  stocks占位). They are NEVER estimated from the candidate subset (that would be
  selection-biased and would drift the prompt's "大盘" semantics).

All reads are local file/bin reads; no network.
"""
from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# --------------------------------------------------------------------------
# Dataclasses (forward-compatible: unknown jsonl keys are kept in `extra`)
# --------------------------------------------------------------------------

_NEWS_KNOWN = {"ts", "date", "session", "code", "title", "body", "source",
               "url", "provider"}
_EVENT_KNOWN = {"ann_date", "code", "type", "summary", "fields", "session",
                "source", "v"}
_POLICY_KNOWN = {"pub_date", "ts", "session", "level", "title", "summary",
                 "tags", "code", "source", "url", "provider", "v"}


@dataclass
class NewsItem:
    ts: str
    date: str
    session: str
    code: Optional[str]
    title: str
    body: str
    source: str = ""
    url: Optional[str] = None
    provider: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EventItem:
    ann_date: str
    code: str
    type: str
    summary: str
    fields: Dict[str, Any] = field(default_factory=dict)
    session: str = "pre_open"
    source: str = ""
    v: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyItem:
    """policy.jsonl line (P4). Same ``ts <= boundary`` judge as news.

    ``pub_date`` is the visible-from trading day (redundant, sort/read only —
    NEVER the boundary key). ``ts`` is the authoritative PIT truncation key:
    gov policy = ``...T23:59:59`` (date-only接口, next pre-open可见); macro
    newsflash = real sec ts. ``level`` ∈ {gov, macro_news}.
    """

    pub_date: str
    ts: str
    session: str = "post_close"
    level: str = "gov"
    title: str = ""
    summary: str = ""
    tags: List[str] = field(default_factory=list)
    code: Optional[str] = None
    source: str = ""
    url: Optional[str] = None
    provider: str = ""
    v: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VisibleInfo:
    """Mirror of the ``get_visible_info`` return dict's field set.

    ``ctx`` is intentionally omitted — the stocks contract keeps it ``{}``.
    """

    date: str
    as_of: str
    boundary_ts: str
    news: List[NewsItem]
    events: List[EventItem]
    policy: List[PolicyItem]           # P4: filled via the same ts<=boundary judge
    market_eod_prev: Optional[Dict[str, Any]]


def _normalize_as_of(as_of: str) -> str:
    """'HH:MM' / 'HH:MM:SS' → 'HH:MM:SS' (copied from stocks ``_normalize_as_of``)."""
    parts = str(as_of).split(":")
    if len(parts) == 2:
        return f"{parts[0]:0>2}:{parts[1]:0>2}:00"
    if len(parts) == 3:
        return f"{parts[0]:0>2}:{parts[1]:0>2}:{parts[2]:0>2}"
    raise ValueError(f"as_of 格式非法: {as_of!r} (应为 HH:MM 或 HH:MM:SS)")


def _row_to_news(row: Dict[str, Any]) -> NewsItem:
    extra = {k: v for k, v in row.items() if k not in _NEWS_KNOWN}
    return NewsItem(
        ts=row["ts"], date=row.get("date", ""), session=row.get("session", ""),
        code=row.get("code"), title=row.get("title", ""), body=row.get("body", ""),
        source=row.get("source", ""), url=row.get("url"),
        provider=row.get("provider", ""), extra=extra,
    )


def _row_to_event(row: Dict[str, Any]) -> EventItem:
    extra = {k: v for k, v in row.items() if k not in _EVENT_KNOWN}
    return EventItem(
        ann_date=row["ann_date"], code=row.get("code", ""),
        type=row.get("type", ""), summary=row.get("summary", ""),
        fields=row.get("fields", {}) or {}, session=row.get("session", "pre_open"),
        source=row.get("source", ""), v=int(row.get("v", 1)), extra=extra,
    )


def _row_to_policy(row: Dict[str, Any]) -> PolicyItem:
    extra = {k: v for k, v in row.items() if k not in _POLICY_KNOWN}
    return PolicyItem(
        pub_date=row.get("pub_date", ""), ts=row["ts"],
        session=row.get("session", "post_close"), level=row.get("level", "gov"),
        title=row.get("title", ""), summary=row.get("summary", ""),
        tags=row.get("tags", []) or [], code=row.get("code"),
        source=row.get("source", ""), url=row.get("url"),
        provider=row.get("provider", ""), v=int(row.get("v", 1)), extra=extra,
    )


class PitReader:
    """PIT reader rooted at a pit_store dir + a day loader.

    Parameters
    ----------
    store_root:
        pit_store root. Defaults to ``get_data_paths().pit_store_root``.
    day_loader:
        a ``QlibBinaryLoader`` (or compatible: ``_load_calendar('day')``,
        ``fetch_quote``, ``_read_bin``). Defaults to one built from
        ``get_data_paths().qlib_uri``.
    data_end_probe_code:
        the code whose last non-NaN close defines ``data_end`` (the real data
        末日 used to cap the future-padded calendar). Defaults to SH600519.
    """

    def __init__(self, store_root: Optional[Path] = None,
                 day_loader: Optional[Any] = None,
                 data_end_probe_code: str = "SH600519") -> None:
        if store_root is None or day_loader is None:
            from financial_analyst.data.paths import get_data_paths
            paths = get_data_paths()
            if store_root is None:
                store_root = paths.pit_store_root
            if day_loader is None:
                from financial_analyst.data.loaders.qlib_binary import (
                    QlibBinaryLoader,
                )
                day_loader = QlibBinaryLoader(paths.qlib_uri)
        self._root = Path(store_root)
        self._loader = day_loader
        self._probe = data_end_probe_code
        self._cal_day: Optional[List[pd.Timestamp]] = None
        self._cal_day_str: Optional[List[str]] = None
        self._data_end: Optional[pd.Timestamp] = None
        self._meb_cache: Dict[str, dict] = {}
        self._meta: Optional[dict] = None

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def _cal(self) -> List[pd.Timestamp]:
        if self._cal_day is None:
            self._cal_day = list(self._loader._load_calendar("day"))
            self._cal_day_str = [str(d.date()) for d in self._cal_day]
        return self._cal_day

    def _cal_str(self) -> List[str]:
        self._cal()
        return self._cal_day_str  # type: ignore[return-value]

    def data_end(self) -> pd.Timestamp:
        """Real data末日 = last non-NaN day close of the probe code (cached)."""
        if self._data_end is None:
            df = self._loader.fetch_quote(self._probe, "1990-01-01",
                                          "2099-12-31", "day")
            if df is None or len(df) == 0:
                # fall back to calendar end (degenerate, but never crash)
                self._data_end = self._cal()[-1]
            else:
                col = "close" if "close" in df.columns else df.columns[-1]
                valid = df.dropna(subset=[col]) if col in df.columns else df
                self._data_end = pd.Timestamp(valid["trade_date"].max())
        return self._data_end

    def trading_days(self, start: Optional[str] = None,
                     end: Optional[str] = None) -> List[str]:
        """Trading days in [start, end], **capped to data_end** (§1.6)."""
        cal = self._cal()
        de = self.data_end()
        hi = min(pd.Timestamp(end), de) if end else de
        lo = pd.Timestamp(start) if start else cal[0]
        return [str(d.date()) for d in cal if lo <= d <= hi]

    def prev_trade_date(self, date: str) -> Optional[str]:
        """The last trading day strictly < ``date`` (bisect; skips holidays)."""
        days = self._cal_str()
        i = bisect.bisect_left(days, date)
        return days[i - 1] if i > 0 else None

    def is_trade_day(self, date: str) -> bool:
        days = self._cal_str()
        i = bisect.bisect_left(days, date)
        return i < len(days) and days[i] == date

    def _candidate_days(self, date: str, lookback_days: int) -> List[str]:
        """``[prev .. date]`` — date plus lookback_days prior trading days."""
        days = self._cal_str()
        i = bisect.bisect_left(days, date)
        if i >= len(days) or days[i] != date:
            return []  # non-trade-day: caller (is_trade_day guard) handles it
        n = lookback_days + 1
        lo = max(0, i + 1 - n)
        return days[lo:i + 1]

    # ------------------------------------------------------------------
    # jsonl loading
    # ------------------------------------------------------------------

    def _load_jsonl_day(self, date: str, kind: str) -> List[Dict[str, Any]]:
        path = self._root / date / f"{kind}.jsonl"
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue  # skip malformed line, never raise
        return rows

    # ------------------------------------------------------------------
    # market_eod_prev — P2 all-None placeholder (§1.4)
    # ------------------------------------------------------------------

    def _market_eod_prev(self, date: str) -> Dict[str, Any]:
        # market_eod_prev 永不来自候选池子集; 真实宽度需中性全市场 universe,
        # fa 无 csiall API → backlog. 字段名与 stocks 占位完全一致, 值全 None.
        if date in self._meb_cache:
            return self._meb_cache[date]
        prev = self.prev_trade_date(date)
        out = {"prev_trade_date": prev, "pct_up_5d": None,
               "median_ret_5d": None, "median_ret_20d": None}
        self._meb_cache[date] = out
        return out

    # ------------------------------------------------------------------
    # main API
    # ------------------------------------------------------------------

    def get_visible_info(self, date, codes=None, as_of: str = "09:25",
                         lookback_days: int = 1,
                         include=("news", "events", "policy", "market_eod_prev")
                         ) -> VisibleInfo:
        """Everything visible at ``date`` ``as_of`` (mirrors stocks R1-R8)."""
        date = str(date)
        as_of_norm = _normalize_as_of(as_of)
        boundary_ts = pd.Timestamp(f"{date} {as_of_norm}")

        meb = (self._market_eod_prev(date)
               if "market_eod_prev" in include else None)
        vi = VisibleInfo(
            date=date, as_of=as_of_norm,
            boundary_ts=boundary_ts.strftime("%Y-%m-%dT%H:%M:%S"),
            news=[], events=[], policy=[], market_eod_prev=meb,
        )

        # non-trade-day → empty lists (market_eod_prev already computed) [R7]
        if not self.is_trade_day(date):
            return vi

        cand_days = self._candidate_days(date, lookback_days)
        if not cand_days:
            return vi

        code_set = set(codes) if codes else None

        # ---- news [R1/R5/R6]: single boundary judge, pd.Timestamp compare ----
        if "news" in include:
            news_rows: List[Dict[str, Any]] = []
            for d in cand_days:
                for row in self._load_jsonl_day(d, "news"):
                    ts = pd.Timestamp(row["ts"])
                    if ts > boundary_ts:                 # R1: only judge
                        continue
                    if code_set is not None:
                        c = row.get("code")
                        if not (c in code_set or c is None):  # R6
                            continue
                    news_rows.append(row)
            news_rows.sort(key=lambda r: r["ts"])        # R5: sort by ts
            vi.news = [_row_to_news(r) for r in news_rows]

        # ---- events [R4/R5/R6]: ann_date <= date, independent of as_of ----
        if "events" in include:
            ev_rows: List[Dict[str, Any]] = []
            date_ts = pd.Timestamp(date)
            for d in cand_days:
                for row in self._load_jsonl_day(d, "events"):
                    if pd.Timestamp(row["ann_date"]) > date_ts:  # R4
                        continue
                    if code_set is not None and row.get("code") not in code_set:
                        continue
                    ev_rows.append(row)
            ev_rows.sort(key=lambda r: r["ann_date"])    # R5: sort by ann_date
            vi.events = [_row_to_event(r) for r in ev_rows]

        # ---- policy [P4]: same ts<=boundary judge as news; news-style code filter ----
        # gov policy is code=null (market-wide) → MUST keep on any code_set (news
        # style); events-style (drop null) would wipe every gov policy. Mirrors
        # stocks pit_store get_visible_info policy block exactly.
        if "policy" in include:
            pol_rows: List[Dict[str, Any]] = []
            for d in cand_days:
                for row in self._load_jsonl_day(d, "policy"):
                    if pd.Timestamp(row["ts"]) > boundary_ts:
                        continue
                    if code_set is not None:
                        c = row.get("code")
                        if not (c in code_set or c is None):  # null = market-wide kept
                            continue
                    pol_rows.append(row)
            pol_rows.sort(key=lambda r: r["ts"])
            vi.policy = [_row_to_policy(r) for r in pol_rows]

        return vi

    def news_date_max(self) -> Optional[str]:
        """``_meta.json['news_date_max']`` (the last day with real news)."""
        if self._meta is None:
            meta_path = self._root / "_meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        self._meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    self._meta = {}
            else:
                self._meta = {}
        return self._meta.get("news_date_max")

    # ------------------------------------------------------------------
    # ≤T-1 / intraday loader封装 (§1.5: the only data闸门)
    # ------------------------------------------------------------------

    def fetch_quote_leq_prev(self, code: str, n_days_back: int = 30,
                             freq: str = "day",
                             as_of_date: Optional[str] = None) -> pd.DataFrame:
        """``end = prev_trade_date(as_of_date)`` → strictly ≤ T-1.

        ``start`` is rolled back ``n_days_back`` *trading days* (not calendar
        days) so a stock with exactly 21 points is not dropped (§2.2 fix).
        """
        if as_of_date is None:
            return pd.DataFrame()
        prev = self.prev_trade_date(as_of_date)
        if prev is None:
            return pd.DataFrame()
        days = self._cal_str()
        i = bisect.bisect_right(days, prev)              # days[:i] are <= prev
        lo_idx = max(0, i - n_days_back)
        start = days[lo_idx]
        return self._loader.fetch_quote(code, start, prev, freq)

    def fetch_bars_intraday(self, code: str, date: str,
                            freq: str = "5min") -> pd.DataFrame:
        """Day-T intraday bars (matching uses these; T-day visibility is fine
        for execution, not for the decision input)."""
        return self._loader.fetch_quote(code, date, date, freq)
