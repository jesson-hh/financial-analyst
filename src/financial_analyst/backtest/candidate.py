"""Candidate pool pre-filter (§246 degraded form).

The universe for a decision day is ``holdings ∪ watchlist ∪ rev_20 top-N``,
where rev_20 is the 20-trading-day reversal computed off **≤T-1** close only.
This deliberately does NOT run the full 5000+ market (that is the stocks
universe; fa has no csiall API) and does NOT touch any T-day data — the single
market-data source is ``reader.fetch_quote_leq_prev(end=prev)`` plus the static
``watchlist.parquet``.

rev_20 picks the *lowest* reversal by default (``rev20_pick='low'`` = nsmallest):
A 股 reversal is the strongest factor and the project rule is "rev_20 越低越值得
关注, 不追涨". Flip to ``'high'`` only deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class CandidateConfig:
    topn: int = 20
    rev20_lookback_tradedays: int = 30   # trading-day rollback (≥21 points)
    rev20_pick: str = "low"              # "low"=nsmallest (reversal); "high"=nlargest
    include_holdings: bool = True
    include_watchlist: bool = True
    watchlist_path: Optional[Path] = None
    sentinel_codes: tuple = ("SH999999",)


@dataclass
class CandidateResult:
    codes: List[str]                     # holdings ∪ watchlist ∪ rev20_top, deduped, holdings first
    rev20_rank: Dict[str, float]         # code -> cross-sectional pct rank (0..1)
    universe_source: Dict[str, str]      # code -> 'holding'|'watchlist'|'rev20_top'
    asof_prev: str                       # the ≤T-1 cut date (= prev_trade_date)


def _load_watchlist_codes(cfg: CandidateConfig) -> List[str]:
    if not cfg.include_watchlist:
        return []
    path = cfg.watchlist_path
    if path is None:
        try:
            from financial_analyst.data.paths import get_data_paths
            path = Path(get_data_paths().parquet_root) / "watchlist.parquet"
        except Exception:
            return []
    path = Path(path)
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
    except Exception:
        return []
    if "code" not in df.columns:
        return []
    return [str(c) for c in df["code"].tolist()]


def select_candidates(date: str, holdings: List[str], reader,
                      cfg: CandidateConfig = CandidateConfig()) -> CandidateResult:
    """Build the candidate pool for ``date`` using only ≤T-1 data."""
    date = str(date)
    prev = reader.prev_trade_date(date)

    holdings = list(dict.fromkeys(holdings)) if cfg.include_holdings else []
    sentinels = set(cfg.sentinel_codes)
    watch = [c for c in _load_watchlist_codes(cfg) if c not in sentinels]

    # base universe for rev_20 = holdings ∪ watchlist (deduped)
    base: List[str] = list(dict.fromkeys([*holdings, *watch]))

    raw_rev20: Dict[str, float] = {}
    for code in base:
        df = reader.fetch_quote_leq_prev(
            code, n_days_back=cfg.rev20_lookback_tradedays,
            freq="day", as_of_date=date)
        if df is None or len(df) == 0 or "close" not in df.columns:
            continue
        df = df.sort_values("trade_date")
        close = df["close"].dropna()
        if len(close) >= 21:
            raw_rev20[code] = float(close.iloc[-1] / close.iloc[-21] - 1.0)

    rev20_rank: Dict[str, float] = {}
    rev20_top: List[str] = []
    if raw_rev20:
        s = pd.Series(raw_rev20)
        rev20_rank = {k: float(v) for k, v in s.rank(pct=True).to_dict().items()}
        picked = (s.nsmallest(cfg.topn) if cfg.rev20_pick == "low"
                  else s.nlargest(cfg.topn))
        rev20_top = list(picked.index)

    # union, holdings first, then rev20_top, then remaining watchlist
    ordered: List[str] = []
    source: Dict[str, str] = {}
    for c in holdings:
        if c not in source:
            ordered.append(c)
            source[c] = "holding"
    for c in rev20_top:
        if c not in source:
            ordered.append(c)
            source[c] = "rev20_top"
    for c in watch:
        if c not in source:
            ordered.append(c)
            source[c] = "watchlist"

    return CandidateResult(
        codes=ordered, rev20_rank=rev20_rank, universe_source=source,
        asof_prev=prev if prev is not None else "",
    )
